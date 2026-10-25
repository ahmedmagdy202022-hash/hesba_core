"""T12 screens: the runs, the salaries, the rules, and one run with its lines, payments and reversal."""

from django.contrib import messages as flash
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from entities import scope as entity_scope
from staff.models import Employee

from . import services
from .models import PayrollLine, PayrollPayment, PayrollRun, PayTo, RunStatus, SalaryProfile

WORDS = {
    "ar": {
        "title": "المرتبات", "intro": "كشف مرتبات كل شهر: المرتب والبدلات، والغياب من الحضور، والتأمينات وضريبة كسب العمل. الترحيل بيسجّل المرتبات كمصروف ومستحقات، والفلوس بتخرج لما تدفع.",
        "month": "الشهر", "prepare": "اعمل كشف الشهر", "status": "الحالة", "gross": "الإجمالي", "net": "الصافي", "left": "باقي يتدفع", "open": "فتح", "none": "لسه مفيش كشوف.",
        "salaries": "مرتبات الموظفين", "rules": "قواعد التأمينات والضريبة", "employee": "الموظف", "basic": "الأساسي", "allowances": "البدلات", "insured_wage": "أجر التأمين (اختياري)",
        "insured": "مؤمَّن عليه", "taxed": "عليه ضريبة", "save": "حفظ", "saved": "اتحفظ.", "absent": "أيام غياب", "absence": "خصم الغياب", "other": "خصومات تانية",
        "emp_ins": "تأمينات (الموظف)", "co_ins": "تأمينات (الشركة)", "tax": "ضريبة كسب العمل", "post": "رحّل الكشف", "posted_on": "تاريخ الترحيل", "posted": "اترحّل.",
        "owed": "المستحق", "due": "المطلوب", "paid": "اتدفع", "pay": "ادفع", "cashbox": "من خزنة / بنك", "amount": "المبلغ (فاضي = الباقي كله)", "date": "التاريخ",
        "payments": "الدفعات", "cancel": "إلغاء الدفعة", "reason": "السبب", "reverse": "إلغاء الكشف بقيد عكسي", "reversed": "اتلغى الكشف بقيد عكسي.",
        "cost": "تكلفة المرتبات على الشركة", "back": "المرتبات", "draft_note": "الكشف لسه مسودة: تقدر تعدّل أي سطر قبل الترحيل.", "no_staff": "مفيش موظفين. ضيفهم من «الموظفين».",
        "review": "الأرقام دي حسب القانون المنشور؛ راجعها مع المحاسب كل يناير.", "emp_pct": "تأمينات الموظف %", "co_pct": "تأمينات الشركة %", "min_wage": "أقل أجر تأميني",
        "max_wage": "أقصى أجر تأميني", "exemption": "الإعفاء الشخصي في السنة", "brackets": "شرائح الضريبة في السنة (لحد:نسبة)", "line_saved": "اتعدّل السطر.",
        "pay_to": {"net": "صافي المرتبات للموظفين", "insurance": "التأمينات الاجتماعية", "tax": "ضريبة كسب العمل"},
    },
    "en": {
        "title": "Payroll", "intro": "A payroll for each month: salary and allowances, absences from attendance, social insurance and income tax. Posting books the salaries as a cost and as debts; money leaves when you pay.",
        "month": "Month", "prepare": "Make this month's payroll", "status": "Status", "gross": "Gross", "net": "Net", "left": "Left to pay", "open": "Open", "none": "No payroll yet.",
        "salaries": "Employee salaries", "rules": "Insurance and tax rules", "employee": "Employee", "basic": "Basic", "allowances": "Allowances", "insured_wage": "Insured wage (optional)",
        "insured": "Insured", "taxed": "Taxed", "save": "Save", "saved": "Saved.", "absent": "Absent days", "absence": "Absence deduction", "other": "Other deductions",
        "emp_ins": "Insurance (employee)", "co_ins": "Insurance (company)", "tax": "Income tax", "post": "Post the payroll", "posted_on": "Posting date", "posted": "Posted.",
        "owed": "Owed", "due": "Due", "paid": "Paid", "pay": "Pay", "cashbox": "From cashbox / bank", "amount": "Amount (empty = all that is left)", "date": "Date",
        "payments": "Payments", "cancel": "Cancel payment", "reason": "Reason", "reverse": "Reverse the payroll", "reversed": "The payroll was reversed.",
        "cost": "Payroll cost to the company", "back": "Payroll", "draft_note": "This payroll is a draft: change any line before posting.", "no_staff": "No employees yet. Add them under “Staff”.",
        "review": "These follow the published law; check them with your accountant every January.", "emp_pct": "Employee insurance %", "co_pct": "Company insurance %",
        "min_wage": "Lowest insured wage", "max_wage": "Highest insured wage", "exemption": "Yearly personal exemption", "brackets": "Yearly tax brackets (up to:rate)",
        "line_saved": "Line changed.", "pay_to": {"net": "Net pay to the employees", "insurance": "Social insurance", "tax": "Income tax"},
    },
}
STATUS = {"ar": {"draft": "مسودة", "posted": "مترحّل", "reversed": "اتلغى بقيد عكسي"}, "en": {"draft": "Draft", "posted": "Posted", "reversed": "Reversed"}}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, lang, **extra):
    words = WORDS[lang]
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "status_words": STATUS[lang],
            "can_manage": services.can_manage(request.user), **extra}


def _guard(request):
    if not services.can_view(request.user):
        raise PermissionDenied("Payroll needs cashboxes.view_expenses.")


def _error(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


def run_list(request):
    _guard(request)
    lang = _lang(request)
    if request.method == "POST":
        try:
            run = services.prepare(request.POST.get("month"), request.user, lang)
        except ValidationError as exc:
            flash.error(request, _error(exc))
        else:
            return redirect(f"{reverse('payroll:detail', args=[run.pk])}?lang={lang}")
        return redirect(f"{reverse('payroll:list')}?lang={lang}")
    rows = []
    for run in PayrollRun.objects.all()[:36]:
        totals = services.run_totals(run)
        left = sum(row["left"] for row in services.owed(run).values()) if run.status == RunStatus.POSTED else None
        rows.append({"run": run, "totals": totals, "left": left})
    return render(request, "payroll/list.html", _context(request, lang, rows=rows, this_month=timezone.localdate().strftime("%Y-%m")))


def salaries(request):
    _guard(request)
    lang = _lang(request)
    employees = list(Employee.objects.filter(active=True).order_by("name"))
    if request.method == "POST":
        try:
            for employee in employees:
                key = str(employee.pk)
                if f"basic_{key}" in request.POST:
                    services.save_salary(employee, {"basic": request.POST.get(f"basic_{key}"), "allowances": request.POST.get(f"allowances_{key}"),
                                                    "insured_wage": request.POST.get(f"insured_wage_{key}"), "insured": request.POST.get(f"insured_{key}"),
                                                    "taxed": request.POST.get(f"taxed_{key}")}, request.user, lang)
        except ValidationError as exc:
            flash.error(request, _error(exc))
        else:
            flash.success(request, WORDS[lang]["saved"])
        return redirect(f"{reverse('payroll:salaries')}?lang={lang}")
    profiles = {profile.employee_id: profile for profile in SalaryProfile.objects.filter(employee__in=employees)}
    return render(request, "payroll/salaries.html", _context(request, lang, rows=[(employee, profiles.get(employee.pk)) for employee in employees]))


def rules_screen(request):
    _guard(request)
    lang = _lang(request)
    if request.method == "POST":
        try:
            services.save_rules(request.POST, request.user, lang)
        except ValidationError as exc:
            flash.error(request, _error(exc))
        else:
            flash.success(request, WORDS[lang]["saved"])
        return redirect(f"{reverse('payroll:rules')}?lang={lang}")
    return render(request, "payroll/rules.html", _context(request, lang, rules=services.rules()))


def run_detail(request, pk):
    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    run = get_object_or_404(PayrollRun, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "line":
                services.change_line(get_object_or_404(PayrollLine, pk=request.POST.get("line") or 0, run=run), request.POST, request.user, lang)
                message = words["line_saved"]
            elif action == "post":
                services.post(run, request.user, lang, request.POST.get("posted_on"))
                message = words["posted"]
            elif action == "pay":
                cashbox = entity_scope.cashboxes(Cashbox.objects).filter(active=True, pk=request.POST.get("cashbox") or 0).first()
                if cashbox is None:
                    raise ValidationError(services.MESSAGES[lang]["number"])
                services.pay(run, request.POST.get("pay_to"), cashbox, request.POST.get("amount"), request.user, lang, request.POST.get("paid_on"))
                message = words["saved"]
            elif action == "cancel_payment":
                payment = get_object_or_404(PayrollPayment, pk=request.POST.get("payment") or 0, run=run)
                services.cancel_payment(payment, request.user, request.POST.get("reason"), lang, request.POST.get("cancelled_on"))
                message = words["saved"]
            elif action == "reverse":
                services.reverse(run, request.user, request.POST.get("reason"), lang, request.POST.get("reversed_on"))
                message = words["reversed"]
            else:
                raise ValidationError(services.MESSAGES[lang]["number"])
        except ValidationError as exc:
            flash.error(request, _error(exc))
        else:
            flash.success(request, message)
        return redirect(f"{reverse('payroll:detail', args=[run.pk])}?lang={lang}")
    totals = services.run_totals(run)
    owed = services.owed(run) if run.status == RunStatus.POSTED else {}
    return render(request, "payroll/detail.html", _context(
        request, lang, run=run, lines=run.lines.select_related("employee"), totals=totals, cost=totals["gross"] + totals["company_insurance"],
        owed=[(key, words["pay_to"][key], value) for key, value in owed.items()], payments=run.payments.select_related("operation__source_cashbox"),
        cashboxes=entity_scope.cashboxes(Cashbox.objects).filter(active=True), today=timezone.localdate(), pay_to=PayTo.values))
