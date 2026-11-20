"""ACT-PROFILE-002: the dashboard panel each activity actually works from.

A kitchen watches its open tables, a clinic its day's bookings, a workshop its
production, a contractor its projects. Each panel reads the activity's own
records (read-only) and only appears when its module is on and the viewer may
open the matching section. Every tile links to the screen behind it.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from config.money import money_round
from entities import scope as entity_scope

ZERO = Decimal("0")

#: Which panel an activity (or sub-activity) leads with. Services and schools
#: run on bookings; a shop, wholesale or pharmacy is covered by the analytics.
PANEL_BY_ACTIVITY = {
    "restaurants": "kitchen",
    "medical": "bookings",
    "education": "school",  # EDU-004: a school works from its classes, not from bookings
    "services": "bookings",
    "manufacturing": "production",
    "contracting": "projects",
}

#: The section each panel needs to be visible (same keys as reports.navigation).
PANEL_SECTION = {"kitchen": "restaurant", "bookings": "appointments", "production": "manufacturing", "projects": "projects", "school": "education",
                 "workshop": "workorders"}

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
        "project_dues": "مستحقات على المشاريع", "overdue_certs": "مستخلصات متأخرة", "behind_schedule": "متأخرة عن الجدول", "requests_waiting": "طلبات شراء مستنية موافقة", "open": "افتح", "of": "من",
        "queue_waiting": "مستنيين في الاستقبال دلوقتي", "follow_missed": "متابعات فاتت", "follow_coming": "متابعات جاية (7 أيام)",
        "insurers_owe": "على شركات التأمين", "hours_unbilled": "ساعات لسه ماتفوترتش", "contracts_due": "فترات عقود مستحقة الفوترة",
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
        "project_dues": "Owed on projects", "overdue_certs": "Overdue certificates", "behind_schedule": "Behind schedule", "requests_waiting": "Purchase requests waiting for approval", "open": "Open", "of": "of",
        "queue_waiting": "Waiting at reception now", "follow_missed": "Missed follow-ups", "follow_coming": "Follow-ups due (7 days)",
        "insurers_owe": "Owed by insurers", "hours_unbilled": "Hours not billed yet", "contracts_due": "Contract periods due to bill",
    },
}


def _tile(key, label, value, kind="count", tone="", sub="", url=""):
    return {"key": key, "label": label, "value": value, "kind": kind, "tone": tone, "sub": sub, "url": url}


#: The floor's money tiles (the open orders' value, today's average order) are the whole floor's sales, so they follow the
#: dashboard's own rule for the whole floor's sales figures: whoever may see every user's sales sees them. A waiter (no
#: sales report) and a cashier (their own sales only) keep the counts and lose the money (restaurants video finding F1).
FLOOR_MONEY = "reports.view_all_sales_report"


def _kitchen(words, today, user=None):
    from permissions.services import user_has_permission
    from restaurant.models import DiningTable, Order, OrderLine, OrderStatus

    # Restaurants R12 (HG-108): tables and orders carry their branch, so the floor's counts are the entity being worked in.
    open_orders = entity_scope.scope(Order.objects, "entity").filter(status=OrderStatus.OPEN)
    value = entity_scope.scope(OrderLine.objects, "order__entity").filter(order__status=OrderStatus.OPEN, voided=False).aggregate(
        # R1b (HG-112): a combo's components at what they charge (menu price less their discount; 0.00 on every other line)
        v=Sum(ExpressionWrapper(F("quantity") * (F("unit_price") - F("discount")), output_field=DecimalField(max_digits=18, decimal_places=4))))["v"] or ZERO
    tables = entity_scope.scope(DiningTable.objects, "entity").filter(active=True).count()
    busy = open_orders.exclude(table=None).values("table").distinct().count()
    paid = entity_scope.scope(Order.objects, "invoice__" + entity_scope.SALES_INVOICE).filter(status=OrderStatus.PAID, closed_at__date=today, invoice__status="posted")
    # R2a (HG-111): a bill paid in parts is one order (its open parts are money still to collect, counted in the open value)
    open_orders = open_orders.filter(split_from=None)
    paid_count = paid.annotate(bill=Coalesce("split_from", "id")).values("bill").distinct().count()
    paid_total = paid.aggregate(t=Sum("invoice__total_amount"))["t"] or ZERO
    money = user is None or user_has_permission(user, FLOOR_MONEY)
    tiles = [
        _tile("open_orders", words["open_orders"], open_orders.count(), tone="lead"),
        _tile("open_value", words["open_value"], money_round(value), "money"),
        _tile("tables_busy", words["tables_busy"], f"{busy} {words['of']} {tables}", "text"),
        _tile("paid_today", words["paid_today"], paid_count),
        _tile("avg_order", words["avg_order"], money_round(paid_total / paid_count) if paid_count else ZERO, "money"),
    ]
    return ([tile for tile in tiles if money or tile["kind"] != "money"]), []


def _bookings(words, today, lang):
    from appointments.models import Appointment, AppointmentStatus as S

    day = entity_scope.scope(Appointment.objects, "entity").filter(starts_at__date=today)  # HG-074
    counts = day.aggregate(
        total=Count("id", filter=~Q(status=S.CANCELLED)),
        waiting=Count("id", filter=Q(status__in=[S.BOOKED, S.CONFIRMED, S.ARRIVED])),
        done=Count("id", filter=Q(status=S.DONE)),
        no_show=Count("id", filter=Q(status=S.NO_SHOW)),
    )
    window = entity_scope.scope(Appointment.objects, "entity").filter(starts_at__date__gte=today - timedelta(days=29), starts_at__date__lte=today,
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


def _clinic_tiles(words, today, user):
    """DASH-003: what a clinic follows up on its home: who waits now, the follow-ups, what insurers owe."""

    from permissions.services import user_has_permission

    from medical import insurance, insights, services as medical
    from medical.models import QueueTicket, TicketStatus

    tiles = []
    if medical.can_view(user):
        waiting = QueueTicket.objects.filter(day=today, status=TicketStatus.WAITING).count()
        tiles.append(_tile("queue_waiting", words["queue_waiting"], waiting, tone="lead" if waiting else ""))
    if user_has_permission(user, medical.VIEW):  # the clinic's whole picture, as on its report
        booked = insights._booked_patients(today)
        missed = len(insights.missed_follow_ups(today, booked))
        tiles.append(_tile("follow_missed", words["follow_missed"], missed, tone="bad" if missed else "good"))
        tiles.append(_tile("follow_coming", words["follow_coming"], len(insights.coming_follow_ups(today, booked))))
    if insurance.can_view(user):
        owed = sum((insurance.outstanding(contract) for contract in insurance.contracts(active_only=True)), ZERO)
        tiles.append(_tile("insurers_owe", words["insurers_owe"], money_round(owed), "money", tone="bad" if owed else ""))
    return tiles


def _office_tiles(words, today, user):
    """DASH-003: an office's unbilled hours and the contract periods due to be billed."""

    from permissions.services import user_has_permission

    from agreements import hours, services as agreements

    tiles = []
    if hours.is_hours_business() and user_has_permission(user, "sales.view_sales_invoices"):
        rows = hours.by_customer()
        total = sum((row["hours"] for row in rows), ZERO)
        amount = money_round(sum((row["amount"] for row in rows), ZERO))
        from settings_core.templatetags.hesba_format import money as display_money, qty

        tiles.append(_tile("hours_unbilled", words["hours_unbilled"], qty(total), "text", tone="bad" if total else "",
                           sub=display_money(amount) if total else ""))
    if agreements.is_contract_business() and user_has_permission(user, "sales.view_sales_invoices"):
        due = agreements.due_count(today)
        tiles.append(_tile("contracts_due", words["contracts_due"], due, tone="bad" if due else "good"))
    return tiles


def _production(words, today):
    from manufacturing.models import ProductionRun, Recipe, RunStatus
    from manufacturing.services import plan
    from master_data.models import Location

    runs = entity_scope.scope(ProductionRun.objects, "location__entity").filter(status=RunStatus.POSTED, run_date__gte=today.replace(day=1), run_date__lte=today)
    totals = runs.aggregate(n=Count("id"), units=Sum("output_quantity"), cost=Sum(F("total_cost") + F("conversion_cost")))
    location = entity_scope.locations(Location.objects).filter(active=True, is_default=True).first() or entity_scope.locations(Location.objects).filter(active=True).first()
    short = []
    for recipe in Recipe.objects.filter(active=True).select_related("product")[:30]:
        if location is not None and plan(recipe, Decimal("1"), location)["possible_batches"] < 1:
            short.append(recipe.product.item_name)
    units = totals["units"] or ZERO
    from manufacturing.models import OrderStatus, ProductionOrder

    from manufacturing import schedule

    open_orders = entity_scope.scope(ProductionOrder.objects, "location__entity").filter(status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS))
    # Manufacturing F6b (HG-134): late as the factory home and the schedule count it, past its promised date (the sales order's
    # deliver-by when there is one) or planned to end after it; the dashboard used to count only past the order's own date.
    late = len(schedule.late_orders(open_orders.select_related("sales_order_line__order"), today))
    return [
        _tile("open_orders_mfg", words["open_orders_mfg"], open_orders.count(), tone="bad" if late else "lead",
              sub=f"{late} {words['late_orders']}" if late else words["late_none"]),
        _tile("runs", words["runs"], totals["n"] or 0),
        _tile("units", words["units"], f"{units.normalize():f}" if units else "0", "text"),
        _tile("prod_cost", words["prod_cost"], money_round(totals["cost"] or ZERO), "money"),
        _tile("short_recipes", words["short_recipes"], len(short), tone="bad" if short else "good",
              sub=(words["short_list"] + " " + "، ".join(short[:4])) if short else words["short_none"]),
    ], []


#: The projects panel follows the project pages' own rule (``projects.views``): contract, billing and what is owed need the
#: sales invoices (``can_view_sales``), and which projects lose money needs the profit report (``can_profit``). A site engineer
#: (projects only) keeps the counts: active projects, behind schedule (contracting video finding: their home showed contract
#: values right above «مفيش أرقام تقدر تشوفها بصلاحياتك»).
PROJECT_SALES, PROJECT_PROFIT = "sales.view_sales_invoices", "reports.view_profit_report"
PROJECT_SALES_TILES = ("contract_total", "billed_pct", "project_dues", "overdue_certs")


def _projects(words, today, user=None):
    from permissions.services import user_has_permission
    from projects.models import Project, ProjectStatus
    from projects.services import summary

    active = list(Project.objects.filter(status=ProjectStatus.ACTIVE)[:50])
    sums = [summary(project) for project in active]
    contract = sum((s["contract"] for s in sums), ZERO)
    billed = sum((s["billed"] for s in sums), ZERO)
    losing = [project.name for project, s in zip(active, sums) if s["profit"] < 0]
    dues = sum((s["due"] for s in sums), ZERO)
    # Contracting C3 (HG-124): behind schedule as the projects report reads it: by the schedule where a project has tasks, else billing against the calendar
    from projects.insights import time_share
    from projects.schedule import behind as schedule_behind

    late = [project.name for project, s in zip(active, sums) if schedule_behind(project, s, time_share(project, today), today)[0]]
    tiles = [
        _tile("active_projects", words["active_projects"], len(active), tone="lead"),
        _tile("contract_total", words["contract_total"], money_round(contract), "money"),
        _tile("billed_pct", words["billed_pct"], f"{round(billed / contract * 100)}%" if contract > 0 else "—", "text"),
        _tile("project_dues", words["project_dues"], money_round(dues), "money"),
        _tile("losing", words["losing"], len(losing), tone="bad" if losing else "good", sub="، ".join(losing[:3])),
        _tile("behind_schedule", words["behind_schedule"], len(late), tone="bad" if late else "good", sub="، ".join(late[:3])),
        _overdue_tile(words, today),
    ]
    if user is not None:
        hidden = set() if user_has_permission(user, PROJECT_SALES) else set(PROJECT_SALES_TILES)
        if not user_has_permission(user, PROJECT_PROFIT):
            hidden.add("losing")
        tiles = [tile for tile in tiles if tile["key"] not in hidden]
    return tiles + _requests_tile(words, user), []


def _requests_tile(words, user):
    """Contracting C7 (HG-125): the site's purchase requests waiting for approval, for whoever approves them; it opens the list."""

    from projects import purchase_requests

    if user is None or not purchase_requests.can_decide(user):
        return []
    waiting = purchase_requests.waiting_count(user)
    return [_tile("requests_waiting", words["requests_waiting"], waiting, tone="bad" if waiting else "good", url=reverse("projects:purchase_requests_all"))]


def _overdue_tile(words, today):
    """CONTRACT-003: posted certificates still owed past the credit days."""

    from settings_core.templatetags.hesba_format import money

    from projects.insights import overdue_certificates
    from projects.models import ProjectStatus

    rows, total, _ = overdue_certificates(today, status=ProjectStatus.ACTIVE)  # the active projects, as the tiles beside it
    return _tile("overdue_certs", words["overdue_certs"], len(rows), tone="bad" if rows else "good", sub=money(total) if rows else "")
WORKSHOP_WORDS = {
    "ar": {"title": "مركز الصيانة دلوقتي", "open": "أوامر مفتوحة", "ready": "جاهزة للتسليم", "late": "متأخرة عن ميعادها", "delivered": "اتسلّمت الشهر ده",
           "returns": "رجعت في الضمان (الشهر)", "late_none": "مفيش تأخير"},
    "en": {"title": "The service centre now", "open": "Open orders", "ready": "Ready for pickup", "late": "Past the promised date", "delivered": "Delivered this month",
           "returns": "Warranty returns (month)", "late_none": "nothing late"},
}


def _workshop(lang, today, user=None):
    from workorders.models import OPEN, Status, WorkOrder
    from workorders.services import late, scoped, sync_cancelled, visible_to

    words = WORKSHOP_WORDS["en" if lang == "en" else "ar"]
    sync_cancelled(user)
    # A technician who sees only their own orders gets their own counts (Codex on #190).
    orders = visible_to(user) if user is not None else scoped(WorkOrder.objects)
    first = today.replace(day=1)
    counts = orders.aggregate(open=Count("id", filter=Q(status__in=OPEN)), ready=Count("id", filter=Q(status=Status.READY)),
                              delivered=Count("id", filter=Q(status=Status.DELIVERED, delivered_at__date__gte=first)),
                              returns=Count("id", filter=Q(warranty_of__isnull=False, received_at__date__gte=first)))
    overdue = late(today).filter(pk__in=orders.values("pk")).count()
    return [
        _tile("wo_open", words["open"], counts["open"], tone="lead"),
        _tile("wo_ready", words["ready"], counts["ready"], tone="good" if counts["ready"] else ""),
        _tile("wo_late", words["late"], overdue, tone="bad" if overdue else "good", sub="" if overdue else words["late_none"]),
        _tile("wo_delivered", words["delivered"], counts["delivered"]),
        _tile("wo_returns", words["returns"], counts["returns"], tone="bad" if counts["returns"] else ""),
    ], []


def build_activity_panel(activity, visible_sections, lang="ar", today=None, user=None):
    """The panel for this activity, or None when it has none or it is hidden."""

    kind = PANEL_BY_ACTIVITY.get(activity)
    if activity == "services":
        from workorders.services import is_workshop

        kind = "workshop" if is_workshop() else kind  # SVC-001: a workshop leads with its work orders
    if kind is None or PANEL_SECTION[kind] not in visible_sections:
        return None
    lang = "en" if lang == "en" else "ar"
    words = WORDS[lang]
    today = today or timezone.localdate()
    if kind == "kitchen":
        tiles, lists = _kitchen(words, today, user)
    elif kind == "bookings":
        tiles, lists = _bookings(words, today, lang)
        if activity == "medical":
            tiles = tiles + _clinic_tiles(words, today, user)
        elif activity == "services":
            tiles = tiles + _office_tiles(words, today, user)
    elif kind == "production":
        tiles, lists = _production(words, today)
    elif kind == "school":
        from education.insights import WORDS as SCHOOL_WORDS, panel_tiles

        tiles, lists = panel_tiles(lang, today, user)
        return {"kind": kind, "title": SCHOOL_WORDS[lang]["school"], "tiles": tiles, "lists": lists, "link": reverse("education:today"), "open": words["open"]}
    elif kind == "workshop":
        tiles, lists = _workshop(lang, today, user)
        title = WORKSHOP_WORDS["en" if lang == "en" else "ar"]["title"]
        return {"kind": kind, "title": title, "tiles": tiles, "lists": lists, "link": reverse("workorders:list"), "open": words["open"]}
    else:
        tiles, lists = _projects(words, today, user)
    link = {"kitchen": "restaurant:board", "bookings": "appointments:agenda", "production": "manufacturing:home", "projects": "projects:list"}[kind]
    return {"kind": kind, "title": words[kind], "tiles": tiles, "lists": lists, "link": reverse(link), "open": words["open"]}
