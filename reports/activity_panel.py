"""ACT-PROFILE-002: the dashboard panel each activity actually works from.

A kitchen watches its open tables, a clinic its day's bookings, a workshop its
production, a contractor its projects. Each panel reads the activity's own
records (read-only) and only appears when its module is on and the viewer may
open the matching section. Every tile links to the screen behind it.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.urls import reverse
from django.utils import timezone

from config.money import money_round

ZERO = Decimal("0")

#: Which panel an activity (or sub-activity) leads with. Services and schools
#: run on bookings; a shop, wholesale or pharmacy is covered by the analytics.
PANEL_BY_ACTIVITY = {
    "restaurants": "kitchen",
    "medical": "bookings",
    "education": "bookings",
    "services": "bookings",
    "manufacturing": "production",
    "contracting": "projects",
}

#: The section each panel needs to be visible (same keys as reports.navigation).
PANEL_SECTION = {"kitchen": "restaurant", "bookings": "appointments", "production": "manufacturing", "projects": "projects"}

WORDS = {
    "ar": {
        "kitchen": "الصالة والمطبخ دلوقتي", "bookings": "حجوزات النهارده", "production": "الإنتاج الشهر ده", "projects": "المشاريع الشغالة",
        "open_orders": "طلبات مفتوحة", "open_value": "قيمة الطلبات المفتوحة", "tables_busy": "طاولات مشغولة", "paid_today": "طلبات اتقفلت النهارده",
        "avg_order": "متوسط الطلب النهارده",
        "today_total": "مواعيد النهارده", "waiting": "لسه جايين / جوّه", "done_today": "خلصت", "no_show_today": "ماجوش",
        "no_show_rate": "نسبة اللي ماجوش (30 يوم)", "next": "الموعد الجاي", "none_next": "مفيش مواعيد تانية النهارده",
        "runs": "تشغيلات", "units": "وحدات اتنتجت", "prod_cost": "تكلفة الإنتاج", "short_recipes": "وصفات ناقصها خامات", "open_orders_mfg": "أوامر إنتاج شغالة", "late_orders": "متأخر عن ميعاده", "late_none": "مفيش تأخير",
        "short_none": "كل الوصفات خاماتها كفاية لتشغيلة", "short_list": "ناقصها خامات:",
        "active_projects": "مشاريع شغالة", "contract_total": "قيمة العقود", "billed_pct": "اتفوتر من العقود", "losing": "مشاريع خسرانة",
        "project_dues": "مستحقات على المشاريع", "open": "افتح", "of": "من",
    },
    "en": {
        "kitchen": "Floor and kitchen now", "bookings": "Today's bookings", "production": "Production this month", "projects": "Running projects",
        "open_orders": "Open orders", "open_value": "Open orders value", "tables_busy": "Tables in use", "paid_today": "Orders closed today",
        "avg_order": "Average order today",
        "today_total": "Bookings today", "waiting": "Still to come / in", "done_today": "Done", "no_show_today": "No-shows",
        "no_show_rate": "No-show rate (30 days)", "next": "Next booking", "none_next": "No more bookings today",
        "runs": "Runs", "units": "Units produced", "prod_cost": "Production cost", "short_recipes": "Recipes short of materials", "open_orders_mfg": "Open production orders", "late_orders": "past their due date", "late_none": "nothing late",
        "short_none": "Every recipe has materials for a batch", "short_list": "Short:",
        "active_projects": "Active projects", "contract_total": "Contract value", "billed_pct": "Billed of contracts", "losing": "Projects losing money",
        "project_dues": "Owed on projects", "open": "Open", "of": "of",
    },
}


def _tile(key, label, value, kind="count", tone="", sub=""):
    return {"key": key, "label": label, "value": value, "kind": kind, "tone": tone, "sub": sub}


def _kitchen(words, today):
    from restaurant.models import DiningTable, Order, OrderLine, OrderStatus

    open_orders = Order.objects.filter(status=OrderStatus.OPEN)
    value = OrderLine.objects.filter(order__status=OrderStatus.OPEN, voided=False).aggregate(
        v=Sum(ExpressionWrapper(F("quantity") * F("unit_price"), output_field=DecimalField(max_digits=18, decimal_places=4))))["v"] or ZERO
    tables = DiningTable.objects.filter(active=True).count()
    busy = open_orders.exclude(table=None).values("table").distinct().count()
    paid = Order.objects.filter(status=OrderStatus.PAID, closed_at__date=today, invoice__status="posted")
    paid_count = paid.count()
    paid_total = paid.aggregate(t=Sum("invoice__total_amount"))["t"] or ZERO
    return [
        _tile("open_orders", words["open_orders"], open_orders.count(), tone="lead"),
        _tile("open_value", words["open_value"], money_round(value), "money"),
        _tile("tables_busy", words["tables_busy"], f"{busy} {words['of']} {tables}", "text"),
        _tile("paid_today", words["paid_today"], paid_count),
        _tile("avg_order", words["avg_order"], money_round(paid_total / paid_count) if paid_count else ZERO, "money"),
    ], []


def _bookings(words, today, lang):
    from appointments.models import Appointment, AppointmentStatus as S

    day = Appointment.objects.filter(starts_at__date=today)
    counts = day.aggregate(
        total=Count("id", filter=~Q(status=S.CANCELLED)),
        waiting=Count("id", filter=Q(status__in=[S.BOOKED, S.CONFIRMED, S.ARRIVED])),
        done=Count("id", filter=Q(status=S.DONE)),
        no_show=Count("id", filter=Q(status=S.NO_SHOW)),
    )
    window = Appointment.objects.filter(starts_at__date__gte=today - timedelta(days=29), starts_at__date__lte=today,
                                        status__in=[S.DONE, S.NO_SHOW])
    closed = window.count()
    rate = round(window.filter(status=S.NO_SHOW).count() / closed * 100) if closed else None
    upcoming = day.filter(status__in=[S.BOOKED, S.CONFIRMED], starts_at__gte=timezone.now()).select_related("customer").order_by("starts_at").first()
    next_text = f"{timezone.localtime(upcoming.starts_at):%H:%M} · {upcoming.customer.name}" if upcoming else words["none_next"]
    return [
        _tile("today_total", words["today_total"], counts["total"], tone="lead"),
        _tile("waiting", words["waiting"], counts["waiting"]),
        _tile("done_today", words["done_today"], counts["done"], tone="good"),
        _tile("no_show_today", words["no_show_today"], counts["no_show"], tone="bad" if counts["no_show"] else ""),
        _tile("no_show_rate", words["no_show_rate"], f"{rate}%" if rate is not None else "—", "text", tone="bad" if rate and rate >= 15 else ""),
        _tile("next", words["next"], next_text, "text"),
    ], []


def _production(words, today):
    from manufacturing.models import ProductionRun, Recipe, RunStatus
    from manufacturing.services import plan
    from master_data.models import Location

    runs = ProductionRun.objects.filter(status=RunStatus.POSTED, run_date__gte=today.replace(day=1), run_date__lte=today)
    totals = runs.aggregate(n=Count("id"), units=Sum("output_quantity"), cost=Sum("total_cost"))
    location = Location.objects.filter(active=True, is_default=True).first() or Location.objects.filter(active=True).first()
    short = []
    for recipe in Recipe.objects.filter(active=True).select_related("product")[:30]:
        if location is not None and plan(recipe, Decimal("1"), location)["possible_batches"] < 1:
            short.append(recipe.product.item_name)
    units = totals["units"] or ZERO
    from manufacturing.models import OrderStatus, ProductionOrder

    open_orders = ProductionOrder.objects.filter(status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS))
    late = open_orders.filter(due_date__lt=today).count()
    return [
        _tile("open_orders_mfg", words["open_orders_mfg"], open_orders.count(), tone="bad" if late else "lead",
              sub=f"{late} {words['late_orders']}" if late else words["late_none"]),
        _tile("runs", words["runs"], totals["n"] or 0),
        _tile("units", words["units"], f"{units.normalize():f}" if units else "0", "text"),
        _tile("prod_cost", words["prod_cost"], money_round(totals["cost"] or ZERO), "money"),
        _tile("short_recipes", words["short_recipes"], len(short), tone="bad" if short else "good",
              sub=(words["short_list"] + " " + "، ".join(short[:4])) if short else words["short_none"]),
    ], []


def _projects(words):
    from projects.models import Project, ProjectStatus
    from projects.services import summary

    active = list(Project.objects.filter(status=ProjectStatus.ACTIVE)[:50])
    sums = [summary(project) for project in active]
    contract = sum((s["contract"] for s in sums), ZERO)
    billed = sum((s["billed"] for s in sums), ZERO)
    losing = [project.name for project, s in zip(active, sums) if s["profit"] < 0]
    dues = sum((s["due"] for s in sums), ZERO)
    return [
        _tile("active_projects", words["active_projects"], len(active), tone="lead"),
        _tile("contract_total", words["contract_total"], money_round(contract), "money"),
        _tile("billed_pct", words["billed_pct"], f"{round(billed / contract * 100)}%" if contract > 0 else "—", "text"),
        _tile("project_dues", words["project_dues"], money_round(dues), "money"),
        _tile("losing", words["losing"], len(losing), tone="bad" if losing else "good", sub="، ".join(losing[:3])),
    ], []


def build_activity_panel(activity, visible_sections, lang="ar", today=None):
    """The panel for this activity, or None when it has none or it is hidden."""

    kind = PANEL_BY_ACTIVITY.get(activity)
    if kind is None or PANEL_SECTION[kind] not in visible_sections:
        return None
    lang = "en" if lang == "en" else "ar"
    words = WORDS[lang]
    today = today or timezone.localdate()
    if kind == "kitchen":
        tiles, lists = _kitchen(words, today)
    elif kind == "bookings":
        tiles, lists = _bookings(words, today, lang)
    elif kind == "production":
        tiles, lists = _production(words, today)
    else:
        tiles, lists = _projects(words)
    link = {"kitchen": "restaurant:board", "bookings": "appointments:agenda", "production": "manufacturing:home", "projects": "projects:list"}[kind]
    return {"kind": kind, "title": words[kind], "tiles": tiles, "lists": lists, "link": reverse(link), "open": words["open"]}
