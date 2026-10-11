"""SVC-003 screens: session packages (sell, see what is left, use a session) and logged hours (log, bill)."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer, Item
from permissions.services import user_has_permission
from staff.models import Employee

from . import hours as hours_service
from . import packages as package_service
from .models import Package, PackageUse, TimeEntry
from .services import MANAGE, VIEW
from .views import _lang

WORDS = {
    "ar": {"title": "باكدجات الجلسات", "intro": "باكدج جلسات بيتباع على فاتورة بيع واحدة (مسودة لحد ما تترحّل). بعد الترحيل الجلسات بتتسحب واحدة واحدة، ومش بتنزل تحت الصفر.",
           "number": "رقم الباكدج", "customer": "العميل", "service": "الخدمة", "sessions": "عدد الجلسات", "price": "سعر الباكدج (فاضي = سعر الخدمة × الجلسات)",
           "price_short": "السعر", "expires": "ينتهي يوم (اختياري)", "notes": "ملاحظات", "used": "اتعمل", "left": "باقي", "status": "الحالة",
           "sell": "بيع باكدج", "save": "بيع وعمل الفاتورة", "none": "مفيش باكدجات لسه.", "back": "باكدجات الجلسات", "invoice": "الفاتورة",
           "use": "سجّل جلسة", "used_on": "اليوم", "employee": "مين عملها", "nobody": "—", "cancel": "الغِ الجلسة", "cancelled": "اتلغت",
           "uses": "الجلسات اللي اتعملت", "no_uses": "لسه مفيش جلسات.", "sold_total": "جلسات متباعة", "used_total": "اتعمل منها", "left_total": "باقي",
           "value_total": "قيمة الباكدجات المترحّلة", "choose": "اختار", "saved": "اتعمل الباكدج وفاتورته (مسودة). رحّلها من صفحة الفاتورة.",
           "used_ok": "اتسجلت الجلسة.", "cancel_ok": "اتلغت الجلسة ورجعت للباكدج.", "post_first": "رحّل الفاتورة الأول عشان تبدأ تسحب الجلسات.",
           "view_only": "بيع الباكدجات وتسجيل الجلسات للي عنده صلاحية إنشاء فاتورة بيع بس."},
    "en": {"title": "Session packages", "intro": "A package of sessions is sold on one sales invoice (a draft until it is posted). Once posted, sessions are used one by one, never below zero.",
           "number": "Package", "customer": "Customer", "service": "Service", "sessions": "Sessions", "price": "Package price (blank = service price × sessions)",
           "price_short": "Price", "expires": "Expires on (optional)", "notes": "Notes", "used": "Used", "left": "Left", "status": "Status",
           "sell": "Sell a package", "save": "Sell and make the invoice", "none": "No packages yet.", "back": "Session packages", "invoice": "Invoice",
           "use": "Record a session", "used_on": "Day", "employee": "Done by", "nobody": "—", "cancel": "Cancel the session", "cancelled": "Cancelled",
           "uses": "Sessions used", "no_uses": "No sessions yet.", "sold_total": "Sessions sold", "used_total": "Used", "left_total": "Left",
           "value_total": "Posted package value", "choose": "Choose", "saved": "Package and its draft invoice made. Post it from the invoice page.",
           "used_ok": "Session recorded.", "cancel_ok": "Session cancelled and given back.", "post_first": "Post the invoice first to start using sessions.",
           "view_only": "Selling packages and recording sessions needs the create-sales-invoice permission."},
}

HOUR_WORDS = {
    "ar": {"title": "ساعات العمل", "intro": "سجّل الساعات اللي اشتغلتها لكل عميل، وفوترها وقت ما تحب: «فوتر الساعات» بيعمل فاتورة بيع (مسودة) فيها سطر لكل يوم، والساعة متفوترش مرتين.",
           "log": "سجّل ساعات", "customer": "العميل", "service": "الخدمة", "employee": "مين اشتغل", "me": "أنا", "worked_on": "اليوم", "hours": "الساعات",
           "rate": "سعر الساعة (فاضي = سعر الخدمة)", "rate_short": "سعر الساعة", "description": "اشتغلت على إيه", "save": "سجّل", "unbilled": "ساعات لسه ماتفوترتش",
           "amount": "القيمة", "bill": "فوتر الساعات", "remove": "امسح", "none": "مفيش ساعات مستنية فوترة.", "team": "ساعات الفريق الشهر ده",
           "billed_pct": "اتفوتر منها", "recent": "آخر ساعات اتفوترت", "invoice": "الفاتورة", "choose": "اختار", "logged": "اتسجلت الساعات.",
           "billed_ok": "اتعملت الفاتورة {number} (مسودة). رحّلها من صفحتها.", "removed": "اتمسحت.", "no_team": "لسه مفيش ساعات الشهر ده.",
           "view_only": "فوترة الساعات للي عنده صلاحية إنشاء فاتورة بيع بس."},
    "en": {"title": "Hours worked", "intro": "Log the hours worked for each customer and bill them when you like: “Bill the hours” makes a draft sales invoice with a line a day, and an hour is never billed twice.",
           "log": "Log hours", "customer": "Customer", "service": "Service", "employee": "Who worked", "me": "Me", "worked_on": "Day", "hours": "Hours",
           "rate": "Hourly rate (blank = service price)", "rate_short": "Hourly rate", "description": "What was done", "save": "Log", "unbilled": "Hours not billed yet",
           "amount": "Amount", "bill": "Bill the hours", "remove": "Remove", "none": "No hours waiting to be billed.", "team": "Team hours this month",
           "billed_pct": "Billed", "recent": "Recently billed hours", "invoice": "Invoice", "choose": "Choose", "logged": "Hours logged.",
           "billed_ok": "Invoice {number} made (draft). Post it from its page.", "removed": "Removed.", "no_team": "No hours this month yet.",
           "view_only": "Billing hours needs the create-sales-invoice permission."},
}


def _customers():
    return Customer.objects.filter(active=True).exclude(customer_code="WALK-IN").order_by("name")


def _services():
    return Item.objects.filter(active=True, is_stock_tracked=False).order_by("item_name")


def _employees():
    return Employee.objects.filter(active=True).order_by("name")


def _number(raw):
    """A posted id, or 0 (which finds nothing) when it is not a number."""

    return int(raw) if str(raw or "").isdigit() else 0


def _pick(model, raw, **filters):
    return model.objects.filter(pk=raw or 0, **filters).first() if str(raw or "").isdigit() else None


def _package_guard(request):
    if not package_service.is_package_business():
        raise Http404("Session packages are for salons and service businesses.")
    if not user_has_permission(request.user, VIEW):
        raise PermissionDenied("Session packages need sales.view_sales_invoices.")


def _package_base(request, lang, **extra):
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"],
            "can_manage": user_has_permission(request.user, MANAGE), "customers": _customers(), "services_list": _services(),
            "employees": _employees(), "today": timezone.localdate(), **extra}


def package_list(request):
    _package_guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    error = ""
    if request.method == "POST":
        data = request.POST.dict()
        data["customer"] = _pick(Customer, request.POST.get("customer"), active=True)
        data["service"] = _pick(Item, request.POST.get("service"), active=True, is_stock_tracked=False)
        try:
            package = package_service.sell_package(data, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('agreements:package', args=[package.pk])}?lang={lang}")
    every = package_service.with_used(Package.objects.select_related("customer", "service", "invoice"))
    packages = list(every[:300])
    labels = package_service.STATE_LABELS[lang]
    rows = []
    for package in packages:
        status = package_service.state(package)
        rows.append({"package": package, "used": package_service.used(package), "left": package_service.remaining(package), "state": status,
                     "state_label": labels[status]})
    return render(request, "agreements/packages.html", _package_base(request, lang, rows=rows, totals=package_service.totals(every), error=error,
                                                                    post=request.POST if error else {}))


def package_detail(request, pk):
    _package_guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    package = get_object_or_404(Package.objects.select_related("customer", "service", "invoice"), pk=pk)
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") == "cancel_use":
                use = get_object_or_404(PackageUse, pk=_number(request.POST.get("use")), package=package)
                package_service.cancel_use(use, request.user, lang)
                messages.success(request, words["cancel_ok"])
            else:
                package_service.use_session(package, request.user, used_on=request.POST.get("used_on") or None,
                                            employee=_pick(Employee, request.POST.get("employee"), active=True), notes=request.POST.get("notes", ""), lang=lang)
                messages.success(request, words["used_ok"])
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            return redirect(f"{reverse('agreements:package', args=[package.pk])}?lang={lang}")
    status = package_service.state(package)
    return render(request, "agreements/package.html", _package_base(request, lang, package=package, uses=package.uses.select_related("employee"),
                                                                   used=package_service.used(package), left=package_service.remaining(package),
                                                                   state=status, state_label=package_service.STATE_LABELS[lang][status], error=error))


# ---- hours ----

def _hours_guard(request):
    if not hours_service.is_hours_business():
        raise Http404("Hours are for offices and service businesses.")
    if not hours_service.can_view(request.user):
        raise PermissionDenied("Hours need sales.view_sales_invoices or a linked employee.")


def hours_home(request):
    _hours_guard(request)
    lang = _lang(request)
    words = HOUR_WORDS[lang]
    manager = user_has_permission(request.user, MANAGE)
    sees_all = user_has_permission(request.user, VIEW)
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "bill":
                customer = get_object_or_404(Customer, pk=_number(request.POST.get("customer")))
                invoice = hours_service.bill_hours(customer, request.user, lang)
                messages.success(request, words["billed_ok"].format(number=invoice.invoice_number))
                return redirect(f"{reverse('sales:detail', args=[invoice.pk])}?lang={lang}")
            if action == "remove":
                hours_service.remove_entry(get_object_or_404(TimeEntry, pk=_number(request.POST.get("entry"))), request.user, lang)
                messages.success(request, words["removed"])
            else:
                data = request.POST.dict()
                data["customer"] = _pick(Customer, request.POST.get("customer"), active=True)
                data["service"] = _pick(Item, request.POST.get("service"), active=True, is_stock_tracked=False)
                data["employee"] = _pick(Employee, request.POST.get("employee"), active=True)
                hours_service.log_time(data, request.user, lang)
                messages.success(request, words["logged"])
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            return redirect(f"{reverse('agreements:hours')}?lang={lang}")
    own = hours_service._employee_of(request.user)
    # an employee without the sales view sees their own hours only, totals included
    groups = hours_service.by_customer() if sees_all else (hours_service.by_customer(employee=own) if own else [])
    today = timezone.localdate()
    team = hours_service.by_employee(today.replace(day=1), today) if sees_all else []
    recent = (TimeEntry.objects.filter(invoice__isnull=False).exclude(invoice__status="cancelled").select_related("customer", "invoice", "employee")[:20]
              if sees_all else [])
    return render(request, "agreements/hours.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "can_manage": manager, "sees_all": sees_all,
        "can_log": hours_service.can_log(request.user), "own": own, "customers": _customers(), "services_list": _services(), "employees": _employees(),
        "groups": groups, "team": team, "recent": recent, "today": today, "error": error, "post": request.POST if error else {}})
