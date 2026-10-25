"""T12 (MARKET-PARITY): payroll — prepare, post, pay and reverse a month's run (HG-060).

* **Prepare:** one line per active employee with a salary: basic + allowances, less the absent and unpaid-leave days
  the attendance sheet counted (a day is a thirtieth of the month), less other deductions = gross. Social insurance
  is counted on the insured wage (the basic salary unless set), held within the legal floor and ceiling. Income tax
  is the yearly tax on (gross − employee insurance) × 12 − the yearly exemption, through the brackets, divided by 12.
  Net = gross − employee insurance − income tax. A draft line can be changed by hand.
* **Post:** books the cost (gross + company insurance) and what is owed: net pay, insurance (both shares) and tax.
  No money moves.
* **Pay:** each payment is a cashbox operation of kind payroll against one debt (net / insurance / tax), never more
  than what is still owed on it, dated when paid. A payment is cancelled with a dated reversal of its operation.
* **Reverse:** a posted run with nothing paid on it is reversed on a date of its own; its accrual entry stays and the
  opposite one is added (append-only).

The rules (rates, floor and ceiling, exemption, brackets) are data on ``PayrollSettings``, since they change by decree.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission

from .models import PayrollLine, PayrollPayment, PayrollRun, PayrollSettings, PayTo, RunStatus, SalaryProfile

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
VIEW = "cashboxes.view_expenses"
MANAGE = "cashboxes.record_expenses"
AMOUNTS = ("gross", "employee_insurance", "company_insurance", "income_tax", "net")

MESSAGES = {
    "ar": {
        "number": "الرقم ده مش مفهوم.", "negative": "المبالغ مينفعش تبقى بالسالب.", "month": "اختار الشهر.", "taken": "مرتبات الشهر ده اتعملت قبل كده.",
        "nobody": "مفيش موظفين ليهم مرتب. اكتب مرتب كل موظف الأول.", "not_draft": "الكشف ده اترحّل خلاص؛ مينفعش يتعدل.", "not_posted": "الكشف لازم يكون مترحّل.",
        "paid": "اتدفع من الكشف ده؛ ألغي الدفعات الأول.", "more": "المبلغ أكتر من المستحق ({left}).", "zero": "المبلغ لازم أكبر من صفر.",
        "reason": "اكتب سبب الإلغاء.", "brackets": "شرائح الضريبة مش مفهومة: كل سطر «لحد:نسبة»، وآخر سطر من غير «لحد».", "date": "التاريخ مش مفهوم.",
        "before": "التاريخ قبل تاريخ الترحيل.", "cancelled": "الدفعة دي اتلغت قبل كده.", "before_paid": "تاريخ الإلغاء قبل تاريخ الدفعة.",
        "entity": "المرتبات بتتدفع من خزنة أو بنك الشركة الرئيسية.", "before_cancel": "التاريخ قبل إلغاء دفعة من الكشف ده.",
    },
    "en": {
        "number": "This number is not understood.", "negative": "Amounts cannot be negative.", "month": "Choose the month.", "taken": "This month's payroll was made already.",
        "nobody": "No employee has a salary. Type each employee's salary first.", "not_draft": "This run is posted; it cannot be changed.", "not_posted": "The run must be posted.",
        "paid": "Payments were made from this run; cancel them first.", "more": "The amount is more than what is owed ({left}).", "zero": "The amount must be above zero.",
        "reason": "Type the reason.", "brackets": "The tax brackets are not understood: one “up to:rate” per line, the last with nothing before the colon.",
        "date": "The date is not understood.", "before": "The date is before the posting date.", "cancelled": "This payment was cancelled already.", "before_paid": "The cancellation is dated before the payment.",
        "entity": "Payroll is paid from a cashbox or bank of the main entity.", "before_cancel": "The date is before a cancelled payment of this run.",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def enabled():
    """Payroll sits with the employees module, like attendance (T11)."""

    from settings_core.setup_services import module_is_enabled

    return module_is_enabled("employees_technicians")


def _main_entity_allowed(user):
    """Payroll is the main entity's (Codex #269): someone kept to other entities by a membership never sees or changes it."""

    from entities.current import allowed_entities, is_group_wide
    from entities.services import is_multi_entity

    if not is_multi_entity() or is_group_wide(user):
        return True
    return any(entity.is_main for entity in allowed_entities(user))


def can_view(user):
    return user_has_permission(user, VIEW) and _main_entity_allowed(user)


def can_manage(user):
    return user_has_permission(user, MANAGE) and _main_entity_allowed(user)


def _require(user, lang):
    if not can_manage(user):
        raise PermissionDenied("Payroll needs cashboxes.record_expenses.")


def _amount(raw, words, default=ZERO):
    text = str(raw if raw is not None else "").replace(",", "").strip()
    if not text:
        return default
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValidationError(words["number"])
    if value < 0:
        raise ValidationError(words["negative"])
    return money_round(value)


def _day(raw, words, default=None):
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValidationError(words["date"])


def _audit(user, action, run, after):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="payroll", action=action, object_type="payroll.PayrollRun",
                            object_id=str(run.pk), before_data={}, after_data=after)


# ---- the rules ----

def rules():
    return PayrollSettings.get()


def brackets(text, lang="ar"):
    """``[(up_to or None, rate), ...]`` from "up to:rate" lines."""

    words = _words(lang)
    rows = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValidationError(words["brackets"])
        top, rate = (part.strip().replace(",", "") for part in line.split(":", 1))
        try:
            rows.append((Decimal(top) if top else None, Decimal(rate)))
        except InvalidOperation:
            raise ValidationError(words["brackets"])
    if not rows or rows[-1][0] is not None or any(top is None for top, _ in rows[:-1]):
        raise ValidationError(words["brackets"])
    tops = [top for top, _ in rows[:-1]]
    if tops != sorted(tops):
        raise ValidationError(words["brackets"])
    return rows


def yearly_tax(taxable, table):
    tax, floor = ZERO, ZERO
    for top, rate in table:
        if taxable <= floor:
            break
        slice_top = taxable if top is None else min(taxable, top)
        tax += (slice_top - floor) * rate / HUNDRED
        if top is None:
            break
        floor = top
    return money_round(tax)


def compute(line, settings=None, profile=None):
    """Fill a line's gross, insurance, tax and net from its basic, allowances and deductions."""

    settings = settings or rules()
    line.gross = money_round(max(line.basic + line.allowances - line.absence_deduction - line.other_deductions, ZERO))
    insured, taxed = (profile.insured, profile.taxed) if profile else (True, True)
    base = profile.insured_wage if profile and profile.insured_wage is not None else line.basic
    if insured and base > 0:
        base = min(max(base, settings.insurance_min_wage), settings.insurance_max_wage)
        line.employee_insurance = money_round(base * settings.employee_insurance_percent / HUNDRED)
        line.company_insurance = money_round(base * settings.company_insurance_percent / HUNDRED)
    else:
        line.employee_insurance = line.company_insurance = ZERO
    if taxed:
        taxable = max((line.gross - line.employee_insurance) * 12 - settings.yearly_exemption, ZERO)
        line.income_tax = money_round(yearly_tax(taxable, brackets(settings.tax_brackets)) / 12)
    else:
        line.income_tax = ZERO
    line.net = money_round(line.gross - line.employee_insurance - line.income_tax)
    return line


@transaction.atomic
def save_rules(data, user, lang="ar"):
    _require(user, lang)
    words = _words(lang)
    settings = rules()
    settings.employee_insurance_percent = _amount(data.get("employee_insurance_percent"), words, settings.employee_insurance_percent)
    settings.company_insurance_percent = _amount(data.get("company_insurance_percent"), words, settings.company_insurance_percent)
    settings.insurance_min_wage = _amount(data.get("insurance_min_wage"), words, settings.insurance_min_wage)
    settings.insurance_max_wage = _amount(data.get("insurance_max_wage"), words, settings.insurance_max_wage)
    settings.yearly_exemption = _amount(data.get("yearly_exemption"), words, settings.yearly_exemption)
    if data.get("tax_brackets") is not None:
        brackets(data.get("tax_brackets"), lang)
        settings.tax_brackets = str(data.get("tax_brackets")).strip()
    if settings.insurance_max_wage < settings.insurance_min_wage:
        raise ValidationError(words["number"])
    settings.updated_by = user
    settings.save()
    return settings


@transaction.atomic
def save_salary(employee, data, user, lang="ar"):
    _require(user, lang)
    words = _words(lang)
    profile, _ = SalaryProfile.objects.get_or_create(employee=employee)
    profile.basic = _amount(data.get("basic"), words)
    profile.allowances = _amount(data.get("allowances"), words)
    profile.insured_wage = _amount(data.get("insured_wage"), words, None)
    profile.insured = data.get("insured") in (True, "1", "on")
    profile.taxed = data.get("taxed") in (True, "1", "on")
    profile.save()
    return profile


# ---- the run ----

def month_of(raw, words):
    text = str(raw or "").strip()
    try:
        year, month = (int(part) for part in text.split("-")[:2])
        return date(year, month, 1)
    except (TypeError, ValueError):
        raise ValidationError(words["month"])


def _month_end(first):
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _absences(employees, first):
    """Absent and unpaid-leave days from the attendance sheet (T11), when that feature is on."""

    from attendance.services import enabled as attendance_on, month_sheet

    if not attendance_on():
        return {}

    # Codex #269: the real day, so days still to come are not absences and a finished month's last day counts.
    sheets = month_sheet(employees, first.year, first.month, today=timezone.localdate())
    return {sheet["employee"].pk: sheet["totals"]["absent"] + sheet["totals"]["unpaid"] for sheet in sheets}


@transaction.atomic
def prepare(raw_month, user, lang="ar"):
    _require(user, lang)
    words = _words(lang)
    first = month_of(raw_month, words)
    if PayrollRun.objects.filter(month=first).exists():
        raise ValidationError(words["taken"])
    profiles = list(SalaryProfile.objects.filter(employee__active=True).select_related("employee").filter(basic__gt=0))
    if not profiles:
        raise ValidationError(words["nobody"])
    run = PayrollRun.objects.create(month=first, created_by=user)
    absent = _absences([profile.employee for profile in profiles], first)
    settings = rules()
    for profile in profiles:
        line = PayrollLine(run=run, employee=profile.employee, basic=profile.basic, allowances=profile.allowances,
                           absent_days=Decimal(absent.get(profile.employee_id, 0)))
        line.absence_deduction = money_round((profile.basic + profile.allowances) / 30 * line.absent_days)
        compute(line, settings, profile)
        line.save()
    _audit(user, "prepare_payroll", run, {"month": first.isoformat(), "lines": len(profiles)})
    return run


@transaction.atomic
def change_line(line, data, user, lang="ar"):
    _require(user, lang)
    words = _words(lang)
    run = PayrollRun.objects.select_for_update().get(pk=line.run_id)  # Codex #269: the same lock as post(), so a posted run never changes
    line = PayrollLine.objects.select_related("run", "employee").select_for_update(of=("self",)).get(pk=line.pk)
    if run.status != RunStatus.DRAFT:
        raise ValidationError(words["not_draft"])
    for field in ("basic", "allowances", "absence_deduction", "other_deductions"):
        if field in data:
            setattr(line, field, _amount(data.get(field), words))
    compute(line, rules(), SalaryProfile.objects.filter(employee=line.employee).first())
    line.save()
    return line


def run_totals(run):
    sums = run.lines.aggregate(**{name: Sum(name) for name in AMOUNTS})
    return {name: sums[name] or ZERO for name in AMOUNTS}


def cost_total(date_from=None, date_to=None):
    """The payroll cost (gross + the company's insurance) posted in the window, less the runs reversed in it: what the
    reports add to the expenses (Codex #269), as the ledger books it. Payroll belongs to the main entity."""

    from entities.current import current_entity
    from entities.services import main_entity

    entity = current_entity()
    if entity is not None and entity.pk != main_entity().pk:
        return ZERO
    total = ZERO
    for field, sign in (("posted_on", 1), ("reversed_on", -1)):
        runs = PayrollRun.objects.exclude(status=RunStatus.DRAFT).exclude(**{f"{field}__isnull": True})
        if date_from:
            runs = runs.filter(**{f"{field}__gte": date_from})
        if date_to:
            runs = runs.filter(**{f"{field}__lte": date_to})
        sums = PayrollLine.objects.filter(run__in=runs).aggregate(gross=Sum("gross"), company=Sum("company_insurance"))
        total += sign * ((sums["gross"] or ZERO) + (sums["company"] or ZERO))
    return money_round(total)


def owed(run):
    """What each debt of a posted run comes to, what was paid on it, and what is left."""

    totals = run_totals(run)
    due = {PayTo.NET: totals["net"], PayTo.INSURANCE: totals["employee_insurance"] + totals["company_insurance"], PayTo.TAX: totals["income_tax"]}
    paid = dict(run.payments.filter(cancelled_on__isnull=True).values("pay_to").annotate(n=Sum("amount")).values_list("pay_to", "n"))
    return {key: {"due": due[key], "paid": paid.get(key) or ZERO, "left": money_round(due[key] - (paid.get(key) or ZERO))} for key in due}


@transaction.atomic
def post(run, user, lang="ar", posted_on=None):
    _require(user, lang)
    words = _words(lang)
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != RunStatus.DRAFT:
        raise ValidationError(words["not_draft"])
    from closing.services import ensure_period_is_open as ensure_period_open  # a closed month stays closed (HG-011)

    today = timezone.localdate()
    day = _day(posted_on, words, min(_month_end(run.month), max(today, run.month)))  # the month's end, or today for a month still running
    ensure_period_open(day)
    run.status, run.posted_on = RunStatus.POSTED, day
    run.save(update_fields=["status", "posted_on"])
    _audit(user, "post_payroll", run, {"posted_on": day.isoformat(), **{key: str(value) for key, value in run_totals(run).items()}})
    return run


@transaction.atomic
def reverse(run, user, reason, lang="ar", reversed_on=None):
    _require(user, lang)
    words = _words(lang)
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != RunStatus.POSTED:
        raise ValidationError(words["not_posted"])
    if not str(reason or "").strip():
        raise ValidationError(words["reason"])
    if run.payments.filter(cancelled_on__isnull=True).exists():
        raise ValidationError(words["paid"])
    from closing.services import ensure_period_is_open as ensure_period_open

    day = _day(reversed_on, words, timezone.localdate())
    if day < run.posted_on:
        raise ValidationError(words["before"])
    last_cancel = run.payments.aggregate(last=Max("cancelled_on"))["last"]
    if last_cancel and day < last_cancel:  # Codex #269: the debt stays while a payment still stands against it
        raise ValidationError(words["before_cancel"])
    ensure_period_open(day)
    run.status, run.reversed_on, run.reversal_reason = RunStatus.REVERSED, day, str(reason).strip()[:255]
    run.save(update_fields=["status", "reversed_on", "reversal_reason"])
    _audit(user, "reverse_payroll", run, {"reversed_on": day.isoformat(), "reason": run.reversal_reason})
    return run


@transaction.atomic
def pay(run, pay_to, cashbox, raw_amount, user, lang="ar", paid_on=None):
    """Pay part or all of one debt of a posted run out of ``cashbox`` (also needs cashboxes.move_cash)."""

    from cashboxes.models import CashboxOperationType
    from cashboxes.services import create_cashbox_operation

    _require(user, lang)
    words = _words(lang)
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != RunStatus.POSTED:
        raise ValidationError(words["not_posted"])
    if pay_to not in PayTo.values:
        raise ValidationError(words["number"])
    from entities.services import main_entity

    if cashbox.entity_id and cashbox.entity_id != main_entity().pk:
        raise ValidationError(words["entity"])  # Codex #269: the debts are the main entity's, so is the money that settles them
    left = owed(run)[pay_to]["left"]
    amount = _amount(raw_amount, words, left)
    if amount <= 0:
        raise ValidationError(words["zero"])
    if amount > left:
        raise ValidationError(words["more"].format(left=f"{left:,.2f}"))
    day = _day(paid_on, words, timezone.localdate())
    if day < run.posted_on:
        raise ValidationError(words["before"])
    last_cancel = run.payments.filter(pay_to=pay_to, cancelled_on__isnull=False).aggregate(last=Max("cancelled_on"))["last"]
    if last_cancel and day < last_cancel:  # Codex #269: the debt a cancellation frees is free only from that day on
        raise ValidationError(words["before_cancel"])
    number = PayrollPayment.objects.filter(run=run).count() + 1
    label = {"net": "صافي المرتبات", "insurance": "التأمينات الاجتماعية", "tax": "ضريبة كسب العمل"}[pay_to]
    operation = create_cashbox_operation(f"PAY-{run.month:%Y%m}-{run.pk}-{number}", day, CashboxOperationType.PAYROLL, amount,
                                         f"{label} — {run.month:%Y-%m}", user, source_cashbox=cashbox)
    return PayrollPayment.objects.create(run=run, pay_to=pay_to, operation=operation, amount=amount, paid_on=day, created_by=user)


@transaction.atomic
def cancel_payment(payment, user, reason, lang="ar", cancelled_on=None):
    from cashboxes.services import cancel_cashbox_operation

    _require(user, lang)
    words = _words(lang)
    payment = PayrollPayment.objects.select_for_update().get(pk=payment.pk)
    if payment.cancelled_on is not None:
        raise ValidationError(words["cancelled"])
    if not str(reason or "").strip():
        raise ValidationError(words["reason"])
    day = _day(cancelled_on, words, timezone.localdate())
    if day < payment.paid_on:
        raise ValidationError(words["before_paid"])  # Codex #269: never back before the money left
    cancel_cashbox_operation(payment.operation_id, day, str(reason).strip(), user)
    payment.cancelled_on = day
    payment.save(update_fields=["cancelled_on"])
    return payment
