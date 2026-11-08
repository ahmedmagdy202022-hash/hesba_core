"""APPT-001 screens: the day's agenda (with booking), one appointment, and the team's performance."""

from datetime import date, datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from approvals.gate import held
from cashboxes.models import Cashbox, CashboxKind
from deposits import services as deposits
from entities import scope as entity_scope
from master_data.models import Customer, Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from printing.company import company_details
from printing.share import whatsapp_link
from sales.short_sales import is_owner
from settings_core.display_labels import choice_label
from staff.models import Employee

from . import services
from .models import Appointment, AppointmentKind, AppointmentStatus


VIEW, BOOK, REPORT = "sales.view_sales_invoices", "sales.create_sales_invoice", "reports.view_all_sales_report"
RECEIVE = deposits.RECEIVE  # S5: taking and refunding a deposit is a collection
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
        # S5 (HG-069): the deposit box and the cancellation policy.
        "deposit": "العربون", "take_deposit": "خد عربون", "deposit_amount": "مبلغ العربون", "deposit_cashbox": "استلمته في", "deposit_taken": "اتسجل عربون {amount} ({payment}).",
        "deposit_payment": "إيصال", "deposit_invoice": "فاتورته", "deposit_rounding": "فرق تقريب فضل رصيد للعميل", "forfeit": "حوّله لرسوم إلغاء", "refund": "رد العربون",
        "refund_reason": "سبب الرد", "forfeited": "العميل لغى متأخر: العربون اتحوّل لفاتورة رسوم إلغاء {number} بقيمة {amount}.",
        "refunded": "العربون اترد للعميل ({payment}).", "refund_needs_permission": "رد العربون محتاج صلاحية تسجيل التحصيل؛ العربون لسه محجوز رصيد للعميل.",
        "credit_left": "العربون لسه محجوز رصيد للعميل؛ حوّله لرسوم إلغاء أو رده من هنا.", "cancel_reason": "إلغاء الحجز {number}",
        "policy": "سياسة الإلغاء", "policy_hours": "الإلغاء قبل الميعاد بأقل من كام ساعة يضيّع العربون؟", "policy_saved": "اتحفظت سياسة الإلغاء: {hours} ساعة.",
        "policy_note": "لو العميل لغى قبل الميعاد بأقل من {hours} ساعة (أو مجاش) العربون يتحوّل لفاتورة رسوم إلغاء؛ قبل كده يترد له.",
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
        # S5 (HG-069): the deposit box and the cancellation policy.
        "deposit": "Deposit", "take_deposit": "Take a deposit", "deposit_amount": "Deposit amount", "deposit_cashbox": "Received in", "deposit_taken": "Deposit {amount} recorded ({payment}).",
        "deposit_payment": "Receipt", "deposit_invoice": "Its invoice", "deposit_rounding": "Rounding left as the customer's credit", "forfeit": "Forfeit as a cancellation fee", "refund": "Refund the deposit",
        "refund_reason": "Refund reason", "forfeited": "Late cancellation: the deposit became cancellation-fee invoice {number} for {amount}.",
        "refunded": "The deposit was refunded ({payment}).", "refund_needs_permission": "Refunding needs the collection permission; the deposit stays held as the customer's credit.",
        "credit_left": "The deposit stays held as the customer's credit; forfeit or refund it from here.", "cancel_reason": "Booking {number} cancelled",
        "policy": "Cancellation policy", "policy_hours": "Cancelling fewer than how many hours before the booking forfeits the deposit?", "policy_saved": "Cancellation policy saved: {hours} hours.",
        "policy_note": "A cancellation fewer than {hours} hours before the booking (or a no-show) turns the deposit into a cancellation-fee invoice; earlier than that it is refunded.",
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
    from settings_core.vocabulary import term

    title = term("appointments", lang)  # AUDIT-3: "الحجوزات" in a clinic, "الحصص والمواعيد" in a school
    words = dict(WORDS[lang], title=title, page_title=title)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": title,
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
    if request.method == "POST" and request.POST.get("action") == "policy":  # S5 (HG-069): the owner sets the cancellation window
        if not is_owner(request.user):
            raise PermissionDenied("Only the account owner sets the cancellation policy.")
        try:
            hours = deposits.set_cancel_hours(request.POST.get("hours", ""), request.user, lang)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, words["policy_saved"].format(hours=hours))
        return redirect(f"{reverse('appointments:agenda')}?lang={lang}&day={day.isoformat()}")
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
    agenda_rows = list(services.day_agenda(day, employee))
    held_deposits = deposits.held_amounts(agenda_rows)
    rows = [{"appointment": appointment, "status_label": choice_label(appointment, "status", lang), "deposit": held_deposits.get(appointment.pk)} for appointment in agenda_rows]
    return render(request, "appointments/agenda.html", _base(
        request, day=day, prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1), today=timezone.localdate(),
        rows=rows, employee=employee, form=form, error=error, cancel_hours=deposits.cancel_hours(), can_set_policy=is_owner(request.user), **_choices(lang),
    ))


def _medical_file(user):
    from medical.services import shows_files

    return shows_files(user)


def _insured(appointment, lang):
    """MED-008: the insured bill already made, or the insurer and share it would be billed at."""

    from medical import insurance

    if not insurance.is_insurance_install():
        return {}
    bill = insurance.bill_of(appointment)
    if bill is not None:
        return {"insured_bill": bill}
    cover = insurance.cover_for(appointment.customer) if not appointment.invoice_id else None
    if cover is None or appointment.service is None:
        return {}
    percent = insurance.percent_for(cover.contract, appointment.service)
    if percent <= 0:
        return {}
    from settings_core.templatetags.hesba_format import money as display_money

    insurer_share, patient_share = insurance.split(appointment.price, percent)
    label = (f"Bill with insurance: {cover.contract.insurer.name} pays {display_money(insurer_share)}, the patient {display_money(patient_share)}" if lang == "en"
             else f"فوتر بالتأمين: {cover.contract.insurer.name} تدفع {display_money(insurer_share)} والمريض {display_money(patient_share)}")
    return {"insured_label": label}


@require_permission(VIEW)
def detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    # HG-074: another entity's appointment answers 404 before any action runs, as every scoped document does (HG-034).
    appointment = entity_scope.get_or_404(Appointment.objects.select_related("customer", "employee", "service", "invoice"), "entity", pk=pk)
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, BOOK):
            raise PermissionDenied("Changing appointments needs sales.create_sales_invoice.")
        action = request.POST.get("action", "")
        try:
            if action == "status":
                status = request.POST.get("status", "")
                deposit = deposits.held_for(appointment) if status in (AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW) else None
                services.set_status(appointment, status, request.user, lang)
                messages.success(request, words["moved"])
                if deposit is not None:  # S5 (HG-069): the cancellation policy decides what happens to the deposit
                    appointment.refresh_from_db()
                    _settle_cancelled_deposit(request, appointment, deposit, lang, words)
            elif action == "deposit":  # S5 (HG-069): a payment on account, linked to this booking
                if not user_has_permission(request.user, RECEIVE):
                    raise PermissionDenied("Taking a deposit needs sales.receive_customer_payment.")
                deposit = deposits.take(appointment, appointment.customer, _pick(Cashbox, request.POST.get("cashbox"), active=True), request.POST.get("amount", ""), request.user, lang)
                messages.success(request, words["deposit_taken"].format(amount=deposit.payment.amount, payment=deposit.payment.payment_number))
            elif action in ("deposit_forfeit", "deposit_refund"):
                deposit = deposits.held_for(appointment)
                if deposit is None:
                    raise ValidationError(deposits.MESSAGES[lang]["not_held"].format(status=""))
                if action == "deposit_forfeit":
                    invoice = deposits.forfeit(deposit, request.user, lang, request.POST.get("reason", ""))
                    messages.success(request, words["forfeited"].format(number=invoice.invoice_number, amount=invoice.total_amount))
                else:
                    _refund(request, deposit, request.POST.get("reason", ""), lang, words, refuse=True)
            elif action == "bill":
                invoice = services.bill(appointment, request.user, lang)
                messages.success(request, words["billed"].format(number=invoice.invoice_number))
                return redirect(f"{reverse('sales:detail', args=[invoice.pk])}?lang={lang}")
            elif action == "bill_insured":  # MED-008: the patient's share and the insurer's, on two ordinary draft invoices
                from medical import insurance

                if not insurance.is_insurance_install():
                    raise PermissionDenied("Insured billing is for medical activities.")
                bill = insurance.bill_insured(appointment, request.user, lang)
                numbers = " + ".join(invoice.invoice_number for invoice in (bill.patient_invoice, bill.insurer_invoice) if invoice is not None)
                messages.success(request, words["billed"].format(number=numbers))
                return redirect(f"{reverse('appointments:detail', args=[appointment.pk])}?lang={lang}")
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
    insured = _insured(appointment, lang)
    return render(request, "appointments/detail.html", _base(
        request, appointment=appointment, **insured, local=local, status_label=choice_label(appointment, "status", lang), reminder=reminder, actions=actions,
        form=form, editable=editable, error=error, medical_file=_medical_file(request.user), can_bill=appointment.status in (AppointmentStatus.ARRIVED, AppointmentStatus.DONE) and not appointment.invoice_id,
        **_deposit_box(request, appointment, lang), **_choices(lang),
    ))


# ---- S5 (HG-069): the deposit box and the cancellation policy -------------------------------------------------------


def _deposit_box(request, appointment, lang):
    """What the detail page shows about the booking's deposit, and which of the three actions it may offer."""

    deposit = deposits.latest_for(appointment)
    held_deposit = deposit if deposit is not None and deposit.status == "held" else None
    open_booking = appointment.status in (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED)
    can_receive = user_has_permission(request.user, RECEIVE)
    cashboxes = entity_scope.cashboxes(Cashbox.objects.filter(active=True, kind__in=(CashboxKind.CASH, CashboxKind.BANK))).order_by("-is_default", "pk")
    return {
        "deposit": deposit, "deposit_status_label": deposits.status_label(deposit, lang) if deposit is not None else "",
        "can_take_deposit": held_deposit is None and open_booking and can_receive, "deposit_cashboxes": cashboxes,
        # A held deposit on a booking that will not be billed any more (cancelled elsewhere, refund refused, invoice cancelled) is settled from here.
        "can_forfeit_deposit": held_deposit is not None and not open_booking and appointment.status != AppointmentStatus.ARRIVED and user_has_permission(request.user, BOOK),
        "can_refund_deposit": held_deposit is not None and not open_booking and appointment.status != AppointmentStatus.ARRIVED and can_receive,
        "refund_blocker": deposits.refund_blocker(held_deposit, lang) if held_deposit is not None else None,
        "cancel_hours": deposits.cancel_hours(), "policy_text": WORDS[lang]["policy_note"].format(hours=deposits.cancel_hours()),
    }


def _refund(request, deposit, reason, lang, words, refuse):
    """Refund through HG-068's gate. With ``refuse`` False (the cancel button) a refusal is a note, not an error."""

    if not user_has_permission(request.user, RECEIVE):
        if refuse:
            raise PermissionDenied("Refunding a deposit needs sales.receive_customer_payment.")
        messages.info(request, words["refund_needs_permission"])
        return
    blocker = deposits.refund_blocker(deposit, lang)
    if blocker:
        if refuse:
            raise ValidationError(blocker)
        messages.warning(request, blocker)
        return
    if held(request, "sales.cancel_payment", deposit.payment_id, {"reason": reason}, deposit.payment.payment_number):  # FB-27
        return
    deposits.refund(deposit, request.user, reason, lang)
    messages.success(request, words["refunded"].format(payment=deposit.payment.payment_number))


def _settle_cancelled_deposit(request, appointment, deposit, lang, words):
    """The policy: late (or no-show) forfeits into a fee invoice; early refunds. Either refusal leaves the deposit held."""

    reason = words["cancel_reason"].format(number=appointment.number)
    if deposits.policy(appointment) == "forfeit":
        if not user_has_permission(request.user, BOOK):
            messages.info(request, words["credit_left"])
            return
        try:
            invoice = deposits.forfeit(deposit, request.user, lang, reason)
        except ValidationError as exc:
            messages.warning(request, " ".join(exc.messages) + " " + words["credit_left"])
            return
        messages.success(request, words["forfeited"].format(number=invoice.invoice_number, amount=invoice.total_amount))
        return
    _refund(request, deposit, reason, lang, words, refuse=False)


@require_permission(REPORT)
def performance(request):
    today = timezone.localdate()
    date_from = _day(request.GET.get("from")) if request.GET.get("from") else today.replace(day=1)
    date_to = _day(request.GET.get("to")) if request.GET.get("to") else today
    rows = services.performance(date_from, date_to)
    return render(request, "appointments/performance.html", _base(request, rows=rows, date_from=date_from, date_to=date_to,
                                                                  totals={key: sum(row[key] for row in rows) for key in ("done", "sales", "commission")}))


def _iso(moment):
    return timezone.localtime(moment).isoformat()


def _json_error(message, status):
    return JsonResponse({"ok": False, "error": message}, status=status)


@require_POST
@require_permission(VIEW)
def move(request, pk):
    """S10: JSON backend of drag-to-reschedule. Same rule as changing an appointment on the detail screen."""

    lang = _lang(request)
    words = services.MESSAGES[lang]
    if not user_has_permission(request.user, BOOK):
        return _json_error(words["forbidden"], 403)
    appointment = entity_scope.get_or_404(Appointment.objects.all(), "entity", pk=pk)  # HG-074: another entity's appointment answers 404
    try:
        new_start = parse_datetime((request.POST.get("start") or "").strip())
    except ValueError:
        new_start = None
    if new_start is None:
        return _json_error(words["bad_start"], 400)
    if timezone.is_naive(new_start):
        new_start = timezone.make_aware(new_start)
    new_staff = None
    raw_staff = (request.POST.get("staff") or "").strip()
    if raw_staff:
        new_staff = _pick(Employee, raw_staff, active=True)
        if new_staff is None:
            return _json_error(words["bad_staff"], 400)
    try:
        appointment = services.move(appointment, request.user, new_start, new_staff, lang)
    except ValidationError as exc:
        return _json_error(" ".join(exc.messages), 409 if exc.code == "overlap" else 400)
    return JsonResponse({"ok": True, "id": appointment.pk, "staff": appointment.employee_id, "start": _iso(appointment.starts_at),
                         "end": _iso(appointment.ends_at), "duration_minutes": appointment.duration_minutes})


@require_GET
@require_permission(VIEW)
def week_json(request):
    """S10: the week's appointments per employee for the future drag calendar. No money fields."""

    lang = _lang(request)
    raw = request.GET.get("start", "")
    try:
        first_day = date.fromisoformat(raw) if raw else timezone.localdate()
    except ValueError:
        return _json_error(services.MESSAGES[lang]["bad_start"], 400)
    groups = {}
    for appointment in services.week_agenda(first_day):
        key = appointment.employee_id
        group = groups.setdefault(key, {"staff": key, "name": appointment.employee.name if appointment.employee else "", "appointments": []})
        group["appointments"].append({
            "id": appointment.pk, "staff": key, "start": _iso(appointment.starts_at), "end": _iso(appointment.ends_at),
            "customer": appointment.customer.name, "service": appointment.service.item_name if appointment.service else "",
            "status": appointment.status, "status_label": choice_label(appointment, "status", lang),
        })
    return JsonResponse({"start": first_day.isoformat(), "end": (first_day + timedelta(days=6)).isoformat(), "staff": list(groups.values())})
