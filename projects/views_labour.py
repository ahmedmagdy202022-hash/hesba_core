"""Contracting C5 (HG-130): the site-worker register and a project's daily-labour tab (the attendance of a day, the days
recorded, the pay-sheet builder for a period, the paid sheets and the printed sheet).

Permissions, no new code:
* reading the register and the tab: the project's own (``projects.view_projects`` or ``sales.view_sales_invoices``);
* recording a day's attendance: ``projects.record_progress`` or ``sales.create_sales_invoice`` (the site engineer records);
* the register's add and edit (the day rate), and paying a sheet: ``cashboxes.record_expenses``, the expense screen's own
  code (the office). The site engineer records days but never sees a rate and cannot pay (403, nothing written; owner
  question 17);
* rates, amounts, the builder, the sheets and the print: ``cashboxes.record_expenses`` or ``cashboxes.view_expenses``.
A worker of another entity answers 404."""

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from entities import scope as entity_scope
from permissions.decorators import require_any_permission
from permissions.services import user_has_permission
from settings_core.ui_messages import translate

from . import access, labour
from .contract_views import _context
from .models import Project, SiteWorker
from .views import PROJECT_SCREENS, _date, _lang

PAY, VIEW_EXPENSES = labour.PAY_PERMISSION, "cashboxes.view_expenses"

WORDS = {
    "ar": {
        "register_title": "سجل العمالة اليومية", "register_intro": "العمال اللي بيشتغلوا في المواقع باليومية: مش موظفين، فمفيش لهم مرتبات ولا تأمينات. أيامهم بتتسجل على المشروع من تاب «العمالة اليومية» فيه.",
        "name": "اسم العامل", "trade": "التخصص", "trade_ph": "مبيّض، نجار، حداد، عامل مساعد…", "phone": "الموبايل", "rate": "اليومية", "status": "الحالة",
        "days_total": "الأيام المسجّلة", "active": "شغّال", "stopped": "موقوف", "save": "حفظ", "add": "إضافة عامل", "edit_title": "تعديل «{name}»",
        "rate_hint": "تغيير اليومية بيمشي على الأيام الجاية بس؛ الأيام اللي اتسجلت بتفضل بيوميتها.", "empty": "لسه مفيش عمال.", "saved": "اتحفظ العامل «{name}».",
        "view_only": "إضافة العمال وتحديد اليومية لصاحب صلاحية تسجيل المصروفات.", "open_register": "سجل العمالة اليومية", "edit": "تعديل", "back": "المشاريع",
        "tab_title": "العمالة اليومية وكشوف الصرف", "tab_intro": "مهندس الموقع بيسجّل حضور العمال كل يوم (يوم كامل أو نص يوم، والإضافي بالساعة). الحسابات بتحدد اليومية وبتصرف الكشف من الخزنة كمصروف واحد، وكل يوم بيتصرف مرة واحدة.",
        "attendance": "حضور يوم", "day": "اليوم", "show_day": "اعرض اليوم", "worker": "العامل", "days": "الحضور", "overtime": "ساعات إضافي",
        "absent": "غياب", "half": "نص يوم", "full": "يوم كامل", "save_day": "احفظ حضور اليوم", "day_saved": "اتحفظ حضور يوم {date}.", "paid_lock": "اتصرف",
        "no_workers": "مفيش عمال شغّالين. أضف العمال في سجل العمالة الأول.", "recorded": "الأيام المسجّلة", "date": "التاريخ", "amount": "القيمة",
        "sheet": "الكشف", "unpaid": "لسه", "no_days": "مفيش أيام متسجّلة على المشروع ده.", "total_days": "إجمالي أيام العمل", "unpaid_days": "أيام لسه ما اتصرفتش",
        "labour_cost": "تكلفة العمالة على المشروع", "builder": "كشف صرف لفترة", "from": "من", "to": "لحد", "build": "احسب الكشف",
        "builder_hint": "الكشف بيجمع الأيام اللي لسه ما اتصرفتش في الفترة. الإضافي = الساعات × اليومية ÷ {hours} ساعات.", "total": "إجمالي الكشف",
        "nothing": "مفيش أيام لسه ما اتصرفتش في الفترة دي.", "cashbox": "الخزنة", "pay_date": "تاريخ الصرف", "pay": "اصرف الكشف",
        "pay_hint": "الصرف بيعمل مصروف واحد «عمالة يومية» من الخزنة ومربوط بالمشروع تحت «العمالة». لو فيه غلط الغي المصروف من شاشة المصروفات، وأيامه بترجع للكشف الجاي.",
        "paid_ok": "اتصرف الكشف رقم {number} بـ {total} ({expense}).", "sheets": "كشوف الصرف", "number": "رقم", "period": "الفترة", "expense": "المصروف",
        "sheet_paid": "اتصرف", "sheet_cancelled": "مصروفه اتلغى", "print": "اطبع الكشف", "no_sheets": "لسه مفيش كشوف صرف.", "closed": "المشروع اتقفل أو اتلغى؛ التسجيل والصرف متوقفين.",
        "money_note": "اليومية والمبالغ والكشوف لصاحب صلاحية المصروفات.", "expenses_off": "شاشة المصروفات مقفولة؛ الصرف متوقف.",
        "print_title": "كشف صرف عمالة يومية", "project": "المشروع", "paid_on": "تاريخ الصرف", "rate_varies": "متغيرة", "in_words": "المبلغ بالحروف",
        "sign_engineer": "مهندس الموقع", "sign_accountant": "المحاسب", "sign_received": "استلم المبلغ", "back_tab": "رجوع", "print_btn": "اطبع",
        "print_hint": "عشان تحفظه PDF اختار \"حفظ كـ PDF\" من شاشة الطباعة.", "workers_count": "عدد العمال",
    },
    "en": {
        "register_title": "Daily labour register", "register_intro": "The people who work on the sites by the day: they are not employees, so there is no payroll or insurance for them. Their days are recorded on a project, from its Daily labour tab.",
        "name": "Worker's name", "trade": "Trade", "trade_ph": "Plasterer, carpenter, steel fixer, helper…", "phone": "Mobile", "rate": "Day rate", "status": "Status",
        "days_total": "Days recorded", "active": "Working", "stopped": "Switched off", "save": "Save", "add": "Add a worker", "edit_title": "Edit “{name}”",
        "rate_hint": "A new day rate applies to the days recorded from now on; days already recorded keep their rate.", "empty": "No workers yet.", "saved": "Worker “{name}” saved.",
        "view_only": "Adding workers and setting day rates is for users who record expenses.", "open_register": "Daily labour register", "edit": "Edit", "back": "Projects",
        "tab_title": "Daily labour and pay sheets", "tab_intro": "The site engineer records who came each day (a full or half day, and overtime by the hour). The office sets the day rates and pays the sheet from a cashbox as one expense; each day is paid once.",
        "attendance": "A day's attendance", "day": "Day", "show_day": "Show the day", "worker": "Worker", "days": "Attendance", "overtime": "Overtime hours",
        "absent": "Absent", "half": "Half day", "full": "Full day", "save_day": "Save the day's attendance", "day_saved": "Attendance of {date} saved.", "paid_lock": "Paid",
        "no_workers": "No working site workers. Add them in the labour register first.", "recorded": "Days recorded", "date": "Date", "amount": "Amount",
        "sheet": "Sheet", "unpaid": "Not yet", "no_days": "No days recorded on this project.", "total_days": "Total days worked", "unpaid_days": "Days not paid yet",
        "labour_cost": "Labour cost on the project", "builder": "Pay sheet for a period", "from": "From", "to": "To", "build": "Work out the sheet",
        "builder_hint": "The sheet adds up the unpaid days in the period. Overtime = hours x day rate / {hours} hours.", "total": "Sheet total",
        "nothing": "There are no unpaid days in this period.", "cashbox": "Cashbox", "pay_date": "Payment date", "pay": "Pay the sheet",
        "pay_hint": "Paying makes one «Daily labour» expense from the cashbox, linked to the project under labour. If it is wrong, cancel the expense on the expenses screen and its days go back for the next sheet.",
        "paid_ok": "Sheet {number} paid: {total} ({expense}).", "sheets": "Pay sheets", "number": "No.", "period": "Period", "expense": "Expense",
        "sheet_paid": "Paid", "sheet_cancelled": "Expense cancelled", "print": "Print the sheet", "no_sheets": "No pay sheets yet.", "closed": "This project is done or cancelled; recording and paying are stopped.",
        "money_note": "Day rates, amounts and sheets are for users with the expenses permission.", "expenses_off": "The expenses screen is switched off; paying is stopped.",
        "print_title": "Daily labour pay sheet", "project": "Project", "paid_on": "Paid on", "rate_varies": "Varies", "in_words": "In words",
        "sign_engineer": "Site engineer", "sign_accountant": "Accountant", "sign_received": "Amount received by", "back_tab": "Back", "print_btn": "Print",
        "print_hint": "To save a PDF, choose \"Save as PDF\" in the print window.", "workers_count": "Workers",
    },
}


def _need(request, *codes):
    if not any(user_has_permission(request.user, code) for code in codes):
        raise PermissionDenied("This action needs " + " or ".join(codes) + ".")


def can_pay(user):
    return user_has_permission(user, PAY)


def can_money(user):
    return can_pay(user) or user_has_permission(user, VIEW_EXPENSES)


def expenses_open():
    from settings_core.module_gate import closed_module

    return closed_module(reverse("expenses:list")) is None


def _flags(user):
    return {"can_money": can_money(user), "can_pay": can_pay(user)}


def _fields(data):
    return {"name": data.get("name", ""), "trade": data.get("trade", ""), "phone": data.get("phone", ""), "day_rate": data.get("day_rate", ""),
            "active": data.get("active", "1") != "0"}


def _register_context(request, lang, **extra):
    from .views import _base

    context = _base(request, **extra)
    context.update(lw=WORDS[lang], **_flags(request.user))
    return context


# ---- the register ----

@require_any_permission(*PROJECT_SCREENS)
def register(request):
    lang = _lang(request)
    words = WORDS[lang]
    error, form = "", {"active": "1"}
    if request.method == "POST":
        _need(request, PAY)
        form = request.POST
        try:
            worker = labour.save_worker(request.user, _fields(request.POST), lang=lang)
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
        else:
            messages.success(request, words["saved"].format(name=worker.name))
            return redirect(f"{reverse('projects:labour_register')}?lang={lang}")
    rows = [{"worker": worker, "days": worker.recorded or 0, "url": f"{reverse('projects:labour_worker', args=[worker.pk])}?lang={lang}"}
            for worker in labour.workers_in_scope(SiteWorker.objects.annotate(recorded=Sum("days__days")))]
    context = _register_context(request, lang, page_title=words["register_title"], rows=rows, error=error, form=form)
    return render(request, "projects/labour_register.html", context)


@require_any_permission(*PROJECT_SCREENS)
def worker_edit(request, wid):
    lang = _lang(request)
    words = WORDS[lang]
    _need(request, PAY)  # the form shows the day rate: the office's
    worker = get_object_or_404(labour.workers_in_scope(), pk=wid)
    error = ""
    form = {"name": worker.name, "trade": worker.trade, "phone": worker.phone, "day_rate": worker.day_rate, "active": "1" if worker.active else "0"}
    if request.method == "POST":
        form = request.POST
        try:
            labour.save_worker(request.user, _fields(request.POST), worker, lang=lang)
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
        else:
            messages.success(request, words["saved"].format(name=request.POST.get("name", worker.name).strip() or worker.name))
            return redirect(f"{reverse('projects:labour_register')}?lang={lang}")
    context = _register_context(request, lang, page_title=words["edit_title"].format(name=worker.name), worker=worker, error=error, form=form)
    return render(request, "projects/labour_worker.html", context)


# ---- a project's labour tab ----

def _project(pk):
    return get_object_or_404(Project.objects.select_related("customer"), pk=pk)


def _attendance(project, work_date):
    """The grid of a day: every working worker of this entity, plus anyone recorded that day, with what is recorded."""

    recorded = {day.worker_id: day for day in labour.days_in_scope(project.labour_days.filter(work_date=work_date))
                .select_related("worker", "pay_sheet__expense__cashbox_operation")}
    workers = list(labour.workers_in_scope(SiteWorker.objects.filter(active=True)))
    workers += [day.worker for day in recorded.values() if day.worker not in workers]
    rows = []
    for worker in sorted(workers, key=lambda w: (w.trade, w.name, w.pk)):
        day = recorded.get(worker.pk)
        rows.append({"worker": worker, "day": day, "days": str(day.days.normalize()) if day else "0",
                     "overtime": format(day.overtime_hours.normalize(), "f") if day and day.overtime_hours else "", "paid": bool(day and labour.is_paid(day))})
    return rows


@require_any_permission(*PROJECT_SCREENS)
def labour_tab(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    today = timezone.localdate()
    work_date = _date(request.GET.get("day")) or today
    period_to = _date(request.GET.get("to")) or today
    period_from = _date(request.GET.get("from")) or (period_to - timedelta(days=6))
    error = ""
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "attendance":
                _need(request, access.RECORD, access.MANAGE)
                posted_date = _date(request.POST.get("work_date"))
                work_date = posted_date or work_date
                entries = []
                for raw in request.POST.getlist("worker"):
                    worker = labour.workers_in_scope().filter(pk=raw).first() if str(raw).isdigit() else None
                    if worker is None:
                        raise Http404  # a worker of another entity (or none at all) is not there for this user
                    # Only the days and the overtime are read: a posted rate is ignored, the worker's own is copied.
                    entries.append((worker, request.POST.get(f"days_{worker.pk}", "0"), request.POST.get(f"overtime_{worker.pk}", "")))
                labour.record_day(project, request.user, work_date=posted_date, entries=entries, lang=lang)
                messages.success(request, words["day_saved"].format(date=work_date.isoformat()))
                return redirect(f"{reverse('projects:labour', args=[project.pk])}?lang={lang}&day={work_date.isoformat()}")
            if action == "pay":
                _need(request, PAY)
                if not expenses_open():
                    raise Http404
                period_from, period_to = _date(request.POST.get("period_from")), _date(request.POST.get("period_to"))
                raw = str(request.POST.get("cashbox") or "")
                cashbox = entity_scope.cashboxes(Cashbox.objects).filter(active=True, pk=raw).first() if raw.isdigit() else None
                if cashbox is None and raw.isdigit():
                    raise Http404  # another entity's cashbox is not there for this user
                sheet = labour.pay_sheet(project, request.user, period_from=period_from, period_to=period_to, cashbox=cashbox,
                                         pay_date=_date(request.POST.get("pay_date")), lang=lang)
                from settings_core.templatetags.hesba_format import money

                messages.success(request, words["paid_ok"].format(number=sheet.number, total=money(sheet.total), expense=sheet.expense.expense_number))
                return redirect(f"{reverse('projects:labour', args=[project.pk])}?lang={lang}&from={sheet.period_from.isoformat()}&to={sheet.period_to.isoformat()}")
            raise Http404
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
    flags = _flags(request.user)
    days = list(labour.days_in_scope(project.labour_days.select_related("worker", "pay_sheet__expense__cashbox_operation"))[:200])
    recorded = [{"day": day, "paid": labour.is_paid(day), "amount": labour.day_amount(day) if flags["can_money"] else None} for day in days]
    totals = labour.days_in_scope(project.labour_days.all()).aggregate(days=Sum("days"))
    unpaid = labour.days_in_scope(project.labour_days.filter(labour.UNPAID)).aggregate(days=Sum("days"))
    context = _context(request, project, "labour", lw=words, error=error, today=today.isoformat(), work_date=work_date.isoformat(),
                       period_from=period_from.isoformat() if period_from else "", period_to=period_to.isoformat() if period_to else "",
                       grid=_attendance(project, work_date), recorded=recorded, total_days=totals["days"] or 0, unpaid_days=unpaid["days"] or 0,
                       can_log=access.can_write(request.user), expenses_open=expenses_open(), **flags)
    if flags["can_money"]:
        hours = labour.day_hours()
        context.update(labour_cost=labour.labour_cost(project), sheets=labour.sheets(project),
                       builder_note=words["builder_hint"].format(hours=format(hours.normalize(), "f")),
                       builder=labour.preview(project, period_from, period_to) if period_from and period_to and period_from <= period_to else None,
                       cashboxes=entity_scope.cashboxes(Cashbox.objects).filter(active=True).order_by("-is_default", "pk"))
    return render(request, "projects/labour.html", context)


@require_any_permission(*PROJECT_SCREENS)
def sheet_print(request, pk, number):
    """The printed pay sheet (A4, Arabic or English, with signatures): for those who see the money only."""

    from printing.amount_words import amount_in_words
    from printing.company import company_details

    _need(request, PAY, VIEW_EXPENSES)
    lang = _lang(request)
    project = _project(pk)
    entry = next((row for row in labour.sheets(project) if row["sheet"].number == number), None)
    if entry is None:
        raise Http404
    sheet = entry["sheet"]
    rows, lines_total = labour.sheet_rows(sheet)
    for row in rows:
        rates = {line["day"].rate for line in row["lines"]}
        row["rate"] = rates.pop() if len(rates) == 1 else None
    company = company_details()
    return render(request, "projects/labour_sheet_print.html", _context(
        request, project, "labour", lw=WORDS[lang], sheet=sheet, paid=entry["paid"], rows=rows, lines_total=lines_total, company=company,
        total_words=amount_in_words(sheet.total, company["currency"], lang),
    ))
