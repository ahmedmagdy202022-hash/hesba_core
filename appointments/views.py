"""APPT-001 screens: the day's agenda (with booking), one appointment, and the team's performance."""

from datetime import date, datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer, Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from printing.company import company_details
from printing.share import whatsapp_link
from settings_core.display_labels import choice_label
from staff.models import Employee

from . import services
from .models import Appointment, AppointmentKind, AppointmentStatus


VIEW, BOOK, REPORT = "sales.view_sales_invoices", "sales.create_sales_invoice", "reports.view_all_sales_report"
WORDS = {
    "ar": {
        "page_title": "المواعيد", "title": "المواعيد والزيارات", "intro": "احجز ميعاد في المحل أو زيارة عند العميل، واربطه بالخدمة والموظف. لما الشغل يخلص، اعمل فاتورته بدوسة.",
        "day": "اليوم", "prev": "اليوم اللي قبله", "next": "اليوم اللي بعده", "today": "النهارده", "all": "كل الموظفين", "employee": "الموظف", "none": "— من غير —",
        "time": "الساعة", "minutes": "المدة (دقيقة)", "customer": "العميل", "service": "الخدمة", "price": "السعر", "kind": "النوع", "address": "العنوان (للزيارة)",
        "notes": "ملاحظات", "status": "الحالة", "book": "احجز", "new": "ميعاد جديد", "empty": "مفيش مواعيد في اليوم ده.", "booked": "اتحجز الميعاد {number}.",
        "back": "المواعيد", "change": "تعديل الميعاد", "save": "حفظ", "saved": "اتحفظ الميعاد.", "moved": "اتغيرت الحالة.", "invoice": "الفاتورة",
        "bill": "اعمل الفاتورة", "billed": "اتعملت مسودة الفاتورة {number}؛ رحّلها وحصّل من شاشة الفاتورة.", "remind": "تذكير واتساب",
        "reminder": "أهلاً {customer}، بنفكّرك بميعادك {service}يوم {date} الساعة {time}. {company}", "view_only": "الحجز والتعديل لصاحب الصلاحية بس.",
        "performance": "أداء الموظفين", "from": "من", "to": "لحد", "show": "عرض", "done": "مواعيد خلصت", "sales": "المبيعات (من غير ضريبة وبعد المرتجع)",
        "commission": "العمولة", "rate": "النسبة", "perf_note": "المبيعات من الفواتير المرحّلة بس. صرف العمولة بيتسجل كمصروف من شاشة المصروفات.",
        "actions": {"confirmed": "تأكيد", "arrived": "بدأ الشغل", "done": "خلص", "cancelled": "إلغاء", "no_show": "مجاش"},
        "bad_when": "اكتب تاريخ وساعة صحيحين.",
    },
    "en": {
        "page_title": "Appointments", "title": "Appointments & visits", "intro": "Book a slot at the shop or a visit at the customer's, with the service and the employee. When the work is done, bill it in one click.",
        "day": "Day", "prev": "Previous day", "next": "Next day", "today": "Today", "all": "All employees", "employee": "Employee", "none": "— none —",
        "time": "Time", "minutes": "Duration (min)", "customer": "Customer", "service": "Service", "price": "Price", "kind": "Type", "address": "Address (for a visit)",
        "notes": "Notes", "status": "Status", "book": "Book", "new": "New appointment", "empty": "No appointments on this day.", "booked": "Appointment {number} booked.",
        "back": "Appointments", "change": "Change the appointment", "save": "Save", "saved": "Appointment saved.", "moved": "Status changed.", "invoice": "Invoice",
        "bill": "Make the invoice", "billed": "Draft invoice {number} made; post it and collect from the invoice screen.", "remind": "WhatsApp reminder",
        "reminder": "Hello {customer}, a reminder of your appointment {service}on {date} at {time}. {company}", "view_only": "Only users with the permission can book or change appointments.",
        "performance": "Team performance", "from": "From", "to": "To", "show": "Show", "done": "Done appointments", "sales": "Sales (before tax, after returns)",
        "commission": "Commission", "rate": "Rate", "perf_note": "Sales come from posted invoices only. Paying a commission is recorded as an expense from the expenses screen.",
        "actions": {"confirmed": "Confirm", "arrived": "Start", "done": "Done", "cancelled": "Cancel", "no_show": "No show"},
        "bad_when": "Enter a valid date and time.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _day(raw):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return timezone.localdate()


def _pick(model, raw, **filters):
    raw = str(raw or "")
    return model.objects.filter(pk=raw, **filters).first() if raw.isdigit() else None


def _form_data(request, words):
    try:
        moment = datetime.combine(date.fromisoformat(request.POST.get("day", "")), datetime.strptime(request.POST.get("time", ""), "%H:%M").time())
        starts_at = timezone.make_aware(moment)
    except (TypeError, ValueError):
        raise ValidationError(words["bad_when"])
    return {
        "customer": _pick(Customer, request.POST.get("customer"), active=True), "employee": _pick(Employee, request.POST.get("employee"), active=True),
        "service": _pick(Item, request.POST.get("service"), active=True), "price": request.POST.get("price", ""), "starts_at": starts_at,
        "duration_minutes": request.POST.get("duration_minutes") or 30, "kind": request.POST.get("kind"), "address": request.POST.get("address", ""),
        "notes": request.POST.get("notes", ""),
    }


def _choices(lang):
    return {
        "customers": Customer.objects.filter(active=True).order_by("name"), "employees": Employee.objects.filter(active=True),
        "services": Item.objects.filter(active=True).order_by("is_stock_tracked", "item_name"),
        "kinds": [(value, "في المحل" if lang == "ar" and value == AppointmentKind.APPOINTMENT else "زيارة عند العميل" if lang == "ar" else label) for value, label in AppointmentKind.choices],
    }


def _base(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"],
               "can_book": user_has_permission(request.user, BOOK), "can_report": user_has_permission(request.user, REPORT)}
    context.update(extra)
    return context


@require_permission(VIEW)
def agenda(request):
    lang = _lang(request)
    words = WORDS[lang]
    day = _day(request.GET.get("day") or request.POST.get("day"))
    employee = _pick(Employee, request.GET.get("employee"))
    error, form = "", {"day": day.isoformat(), "time": "10:00", "duration_minutes": 30, "kind": AppointmentKind.APPOINTMENT}
    if request.method == "POST":
        if not user_has_permission(request.user, BOOK):
            raise PermissionDenied("Booking needs sales.create_sales_invoice.")
        form = request.POST
        try:
            appointment = services.book(_form_data(request, words), request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["booked"].format(number=appointment.number))
            return redirect(f"{reverse('appointments:agenda')}?lang={lang}&day={timezone.localtime(appointment.starts_at).date().isoformat()}")
    rows = [{"appointment": appointment, "status_label": choice_label(appointment, "status", lang)} for appointment in services.day_agenda(day, employee)]
    return render(request, "appointments/agenda.html", _base(
        request, day=day, prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1), today=timezone.localdate(),
        rows=rows, employee=employee, form=form, error=error, **_choices(lang),
    ))


@require_permission(VIEW)
def detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    appointment = get_object_or_404(Appointment.objects.select_related("customer", "employee", "service", "invoice"), pk=pk)
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, BOOK):
            raise PermissionDenied("Changing appointments needs sales.create_sales_invoice.")
        action = request.POST.get("action", "")
        try:
            if action == "status":
                services.set_status(appointment, request.POST.get("status", ""), request.user, lang)
                messages.success(request, words["moved"])
            elif action == "bill":
                invoice = services.bill(appointment, request.user, lang)
                messages.success(request, words["billed"].format(number=invoice.invoice_number))
                return redirect(f"{reverse('sales:detail', args=[invoice.pk])}?lang={lang}")
            else:
                services.reschedule(appointment, _form_data(request, words), request.user, lang)
                messages.success(request, words["saved"])
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            return redirect(f"{reverse('appointments:detail', args=[appointment.pk])}?lang={lang}")
        appointment.refresh_from_db()
    local = timezone.localtime(appointment.starts_at)
    company = company_details()
    reminder = whatsapp_link(appointment.customer.phone, words["reminder"].format(
        customer=appointment.customer.name, service=f"({appointment.service.item_name}) " if appointment.service else "",
        date=local.strftime("%Y-%m-%d"), time=local.strftime("%H:%M"), company=company["name"]))
    actions = [(status, words["actions"][status]) for status in services.NEXT.get(appointment.status, ())]
    form = {"day": local.date().isoformat(), "time": local.strftime("%H:%M"), "duration_minutes": appointment.duration_minutes, "customer": str(appointment.customer_id),
            "employee": str(appointment.employee_id or ""), "service": str(appointment.service_id or ""), "price": appointment.price, "kind": appointment.kind,
            "address": appointment.address, "notes": appointment.notes}
    editable = appointment.status not in (AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW) and not appointment.invoice_id
    return render(request, "appointments/detail.html", _base(
        request, appointment=appointment, local=local, status_label=choice_label(appointment, "status", lang), reminder=reminder, actions=actions,
        form=form, editable=editable, error=error, can_bill=appointment.status in (AppointmentStatus.ARRIVED, AppointmentStatus.DONE) and not appointment.invoice_id,
        **_choices(lang),
    ))


@require_permission(REPORT)
def performance(request):
    today = timezone.localdate()
    date_from = _day(request.GET.get("from")) if request.GET.get("from") else today.replace(day=1)
    date_to = _day(request.GET.get("to")) if request.GET.get("to") else today
    rows = services.performance(date_from, date_to)
    return render(request, "appointments/performance.html", _base(request, rows=rows, date_from=date_from, date_to=date_to,
                                                                  totals={key: sum(row[key] for row in rows) for key in ("done", "sales", "commission")}))
