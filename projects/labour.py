"""Contracting C5 (HG-130): daily labour on a project and its pay sheets.

* **Site workers (``SiteWorker``):** day-workers paid by the day, not employees (no payroll, no insurance; owner
  question 9). The office sets each one's day rate. A worker belongs to an entity; every lookup here goes through the
  entity being worked in.
* **Days (``LabourDay``):** the site engineer records who worked on a project on a day: a full day (1) or half a day
  (0.5), and any overtime hours. The worker's day rate is copied onto the day when it is recorded, so a later rate change
  moves no day already recorded. The engineer never sees a rate (owner question 17).
* **Overtime:** hours x the day rate / the hours of a working day (8 unless the ``projects.labour_day_hours`` setting says
  otherwise). The divisor a sheet was paid with is stored on it.
* **Paying a sheet:** in one transaction, the unchanged ``expenses.services.record_expense`` pays the period's unpaid days
  in one expense, category «عمالة يومية» (made once), from the chosen cashbox (which must be of the entity being worked in
  and pass the project's same-entity rule); the unchanged ``services.link_expense`` links it under «labour»; the days are
  stamped with the sheet. The expense books to its category's account as any expense does (owner question 8, trap 9):
  nothing here touches the ledger or the projector.
* **Paid once:** a day is paid while its sheet's expense is posted. Cancelling that expense the ordinary way (the expenses
  screen) frees its days by being read: nothing is written on cancel, and the next sheet takes them over.
"""

from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope

from . import services
from .models import CostHeading, LabourDay, LabourPaySheet, Project, SiteWorker

ZERO = Decimal("0")
PAY_PERMISSION = "cashboxes.record_expenses"  # the expense screen's own code (expenses.services.RECORD_PERMISSION)
DAY_HOURS_KEY = "projects.labour_day_hours"
DEFAULT_DAY_HOURS = Decimal("8")
CATEGORY_CODE = "daily_labour"
CATEGORY_AR, CATEGORY_EN = "عمالة يومية", "Daily labour"
DAY_VALUES = (Decimal("0.5"), Decimal("1"))
MAX_DAY_HOURS = Decimal("24")
#: A day is unpaid unless a sheet whose expense is still posted holds it (read, never written, on cancel).
UNPAID = Q(pay_sheet__isnull=True) | Q(pay_sheet__expense__isnull=True) | Q(pay_sheet__expense__cashbox_operation__status="cancelled")

MESSAGES = {
    "ar": {
        "name": "اكتب اسم العامل.", "rate": "اكتب اليومية رقم أكبر من صفر.", "worker": "اختار العامل.",
        "inactive": "العامل ده موقوف؛ فعّله الأول من سجل العمالة.", "entity": "العامل ده تابع لكيان تاني؛ اشتغل من الكيان ده.",
        "days": "اختار يوم كامل أو نص يوم.", "overtime": "اكتب ساعات الإضافي رقم صفر أو أكبر.",
        "day_total": "يوم العامل ده مع الإضافي ({total} ساعة) بيعدّي 24 ساعة.", "two_projects": "العامل ده متسجّل له يوم كامل في اليوم ده (على كل المشاريع).",
        "date": "التاريخ مش صحيح.", "future": "مينفعش تسجّل يوم بتاريخ جاي.", "paid_day": "يوم {name} في {date} اتصرف في كشف؛ مينفعش يتغيّر إلا لو مصروف الكشف اتلغى.",
        "period": "اختار فترة صحيحة: من تاريخ لحد تاريخ.", "cashbox": "اختار الخزنة.", "nothing": "مفيش أيام لسه ما اتصرفتش في الفترة دي (الإجمالي 0.00).",
        "worker_entity": "فيه عمال في الفترة دي تابعين لكيان تاني غير كيان الخزنة؛ اصرف كل كيان من خزنته.",
        "category_off": "بند «عمالة يومية» في المصروفات موقوف؛ فعّله من بنود المصروفات الأول.", "permission": "صرف الكشوف لصاحب صلاحية تسجيل المصروفات بس.",
    },
    "en": {
        "name": "Enter the worker's name.", "rate": "Enter a day rate above zero.", "worker": "Choose the worker.",
        "inactive": "This worker is switched off; switch them on in the labour register first.", "entity": "This worker belongs to another entity; work in that entity.",
        "days": "Choose a full day or half a day.", "overtime": "Enter the overtime hours as 0 or more.",
        "day_total": "This worker's day with overtime ({total} hours) passes 24 hours.", "two_projects": "This worker already has a full day on that date (across all projects).",
        "date": "Invalid date.", "future": "A day cannot be recorded for a future date.", "paid_day": "{name}'s day on {date} is on a paid sheet; it can change only if its expense is cancelled.",
        "period": "Choose a valid period: from a date to a date.", "cashbox": "Choose the cashbox.", "nothing": "There are no unpaid days in this period (total 0.00).",
        "worker_entity": "Some workers in this period belong to another entity than the cashbox's; pay each entity from its own cashbox.",
        "category_off": "The «Daily labour» expense category is switched off; switch it on under expense categories first.", "permission": "Only users who record expenses can pay a sheet.",
    },
}


def _audit(project, user, action, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="projects", action=action, object_type="projects.Project",
                            object_id=str(project.pk), after_data=after)


def _decimal(value):
    try:
        number = Decimal(str(value if value not in (None, "") else "0").strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        return None
    return number if number.is_finite() else None


def day_hours():
    """The hours of a working day, the overtime divisor: the ``projects.labour_day_hours`` setting, else 8."""

    from settings_core.models import SystemSetting

    raw = SystemSetting.objects.filter(key=DAY_HOURS_KEY, active=True).values_list("value", flat=True).first()
    hours = _decimal(raw) if raw not in (None, "") else None
    return hours.quantize(Decimal("0.01")) if hours is not None and hours > 0 and hours <= MAX_DAY_HOURS else DEFAULT_DAY_HOURS


def day_amount(day, hours=None):
    """What a recorded day costs: days x rate + overtime hours x rate / the hours of a working day, to the piastre."""

    hours = hours or (day.pay_sheet.day_hours if day.pay_sheet_id else day_hours())
    return money_round(day.days * day.rate + day.overtime_hours * day.rate / hours)


def workers_in_scope(queryset=None):
    """The workers the entity being worked in may use (everyone for the whole group)."""

    return entity_scope.scope(queryset if queryset is not None else SiteWorker.objects.all(), "entity")


def days_in_scope(queryset=None):
    return entity_scope.scope(queryset if queryset is not None else LabourDay.objects.all(), "worker__entity")


def _reach(worker, words):
    if not workers_in_scope(SiteWorker.objects.filter(pk=worker.pk)).exists():
        raise ValidationError(words["entity"])


def is_paid(day):
    sheet = day.pay_sheet
    return sheet is not None and sheet.expense_id is not None and sheet.expense.cashbox_operation.status != "cancelled"


# ---- the register ----

@transaction.atomic
def save_worker(user, data, worker=None, lang="ar"):
    """Add or change a site worker. ``data``: name, trade, phone, day_rate, active. The rate is the office's (the views
    let only ``cashboxes.record_expenses`` holders call this)."""

    words = MESSAGES[lang]
    name = (data.get("name") or "").strip()[:160]
    if not name:
        raise ValidationError(words["name"])
    rate = _decimal(data.get("day_rate"))
    if rate is None or money_round(rate) <= 0:
        raise ValidationError(words["rate"])
    created = worker is None
    if created:
        from entities.current import current_entity

        worker = SiteWorker(created_by=user, entity=current_entity())
    else:
        worker = SiteWorker.objects.select_for_update().get(pk=worker.pk)
        _reach(worker, words)
    before = {} if created else {"day_rate": str(worker.day_rate), "active": worker.active}
    values = {"name": name, "trade": (data.get("trade") or "").strip()[:80], "phone": (data.get("phone") or "").strip()[:40],
              "day_rate": money_round(rate), "active": bool(data.get("active", True))}
    for key, value in values.items():
        setattr(worker, key, value)
    worker.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE if created else AuditEventType.UPDATE, actor=user, module="projects",
                            action="create_site_worker" if created else "change_site_worker", object_type="projects.SiteWorker",
                            object_id=str(worker.pk), before_data=before,
                            after_data={"name": name, "trade": values["trade"], "day_rate": str(worker.day_rate), "active": worker.active})
    return worker


# ---- days ----

def _days_value(raw, words):
    value = _decimal(raw)
    if value is None:
        raise ValidationError(words["days"])
    if value == 0:
        return ZERO
    for allowed in DAY_VALUES:
        if value == allowed:
            return allowed
    raise ValidationError(words["days"])


def _overtime_value(raw, words):
    value = _decimal(raw)
    if value is None or value < 0 or value > MAX_DAY_HOURS:
        raise ValidationError(words["overtime"])
    return value.quantize(Decimal("0.01"))


@transaction.atomic
def record_day(project, user, *, work_date, entries, lang="ar"):
    """The attendance of one day on a project. ``entries``: [(worker, days, overtime_hours)], days being 1, 0.5 or 0 (absent).
    A new day copies the worker's day rate; a changed unpaid day keeps the rate it was recorded at; an absent worker's
    unpaid day is removed. A paid day cannot change. Returns (saved, removed) counts."""

    words = MESSAGES[lang]
    project = services._open(project, services.MESSAGES[lang])
    if work_date is None:
        raise ValidationError(words["date"])
    if work_date > timezone.localdate():
        raise ValidationError(words["future"])
    hours = day_hours()
    saved = removed = 0
    for worker, raw_days, raw_overtime in entries:
        if worker is None:
            raise ValidationError(words["worker"])
        worker = SiteWorker.objects.select_for_update().get(pk=worker.pk)
        _reach(worker, words)
        days = _days_value(raw_days, words)
        overtime = _overtime_value(raw_overtime, words) if days else ZERO
        existing = (LabourDay.objects.select_for_update(of=("self",)).select_related("pay_sheet__expense__cashbox_operation")
                    .filter(project=project, worker=worker, work_date=work_date).first())
        if existing is not None and is_paid(existing):
            if existing.days != days or existing.overtime_hours != overtime:
                raise ValidationError(words["paid_day"].format(name=worker.name, date=work_date.isoformat()))
            continue
        if not days:
            if existing is not None:
                details = {"worker": worker.pk, "date": work_date.isoformat(), "days": str(existing.days)}
                existing.delete()
                removed += 1
                _audit(project, user, "delete_labour_day", details, AuditEventType.DELETE)
            continue
        if existing is not None and existing.days == days and existing.overtime_hours == overtime:
            continue
        if existing is None and not worker.active:
            raise ValidationError(words["inactive"])
        total = days * hours + overtime
        if total > MAX_DAY_HOURS:
            raise ValidationError(words["day_total"].format(total=format(total.normalize(), "f")))
        others = (LabourDay.objects.filter(worker=worker, work_date=work_date).exclude(project=project)
                  .aggregate(days=Sum("days"), overtime=Sum("overtime_hours")))
        elsewhere = others["days"] or ZERO
        if elsewhere + days > 1:
            raise ValidationError(words["two_projects"])
        # Codex #437: the 24-hour limit counts the worker's whole day, every project's days and overtime together
        # (the worker row is locked above, so two projects recording the same day cannot both pass).
        combined = total + elsewhere * hours + (others["overtime"] or ZERO)
        if combined > MAX_DAY_HOURS:
            raise ValidationError(words["day_total"].format(total=format(combined.normalize(), "f")))
        if existing is None:
            day = LabourDay.objects.create(project=project, worker=worker, work_date=work_date, days=days, overtime_hours=overtime,
                                           rate=worker.day_rate, created_by=user)
            _audit(project, user, "record_labour_day", {"worker": worker.pk, "date": work_date.isoformat(), "days": str(days),
                                                         "overtime": str(overtime), "rate": str(day.rate)}, AuditEventType.CREATE)
        else:
            existing.days, existing.overtime_hours = days, overtime
            existing.save(update_fields=["days", "overtime_hours"])
            _audit(project, user, "change_labour_day", {"worker": worker.pk, "date": work_date.isoformat(), "days": str(days),
                                                         "overtime": str(overtime), "rate": str(existing.rate)})
        saved += 1
    return saved, removed


def unpaid_days(project, period_from, period_to):
    """The project's unpaid days in the period, this entity's workers only."""

    return days_in_scope(project.labour_days.filter(UNPAID, work_date__gte=period_from, work_date__lte=period_to))


def _group(days, hours):
    """Days by worker: [{worker, days, overtime, amount, lines}], and the total."""

    rows = OrderedDict()
    for day in sorted(days, key=lambda d: (d.worker.name, d.worker_id, d.work_date)):
        row = rows.setdefault(day.worker_id, {"worker": day.worker, "days": ZERO, "overtime": ZERO, "amount": ZERO, "lines": []})
        amount = day_amount(day, hours)
        row["days"] += day.days
        row["overtime"] += day.overtime_hours
        row["amount"] += amount
        row["lines"].append({"day": day, "amount": amount})
    total = money_round(sum((row["amount"] for row in rows.values()), ZERO))
    return list(rows.values()), total


def preview(project, period_from, period_to):
    """The pay-sheet builder: the unpaid days of the period by worker, with what they come to (0.00 when none)."""

    hours = day_hours()
    rows, total = _group(list(unpaid_days(project, period_from, period_to).select_related("worker")), hours)
    return {"rows": rows, "total": total, "day_hours": hours, "period_from": period_from, "period_to": period_to}


def _category(words):
    from expenses.models import ExpenseCategory

    try:
        with transaction.atomic():  # two first sheets at once: the loser reads the winner's row
            category, _ = ExpenseCategory.objects.get_or_create(code=CATEGORY_CODE, defaults={"name_ar": CATEGORY_AR, "name_en": CATEGORY_EN})
    except IntegrityError:
        category = ExpenseCategory.objects.get(code=CATEGORY_CODE)
    if not category.active:
        raise ValidationError(words["category_off"])
    return category


@transaction.atomic
def pay_sheet(project, user, *, period_from, period_to, cashbox, pay_date=None, lang="ar"):
    """Pay the period's unpaid days in one ordinary expense and keep the sheet. Returns the ``LabourPaySheet``.

    One transaction: the unchanged ``record_expense`` (category «عمالة يومية», from ``cashbox``), the unchanged
    ``link_expense`` (heading «labour»), the days stamped. A second sheet over the same days comes to 0.00 and is refused."""

    from entities.services import main_entity
    from expenses.services import record_expense
    from permissions.services import user_has_permission

    from . import contract

    words = MESSAGES[lang]
    if not user_has_permission(user, PAY_PERMISSION):
        raise PermissionDenied(words["permission"])
    if period_from is None or period_to is None or period_from > period_to:
        raise ValidationError(words["period"])
    if cashbox is None:
        raise ValidationError(words["cashbox"])
    project = services._open(project, services.MESSAGES[lang])  # locks the project: one sheet at a time
    days = list(unpaid_days(project, period_from, period_to).select_for_update(of=("self",)).select_related("worker"))
    contract_words = contract.MESSAGES[lang]
    contract._in_scope(cashbox, contract_words)  # the cashbox is the entity being worked in's
    contract._same_entity(project, cashbox, contract_words)  # and the project's money stays in one entity
    hours = day_hours()
    rows, total = _group(days, hours)
    if not days or total <= 0:
        raise ValidationError(words["nothing"])
    box_entity = cashbox.entity_id or main_entity().pk
    if any((day.worker.entity_id or main_entity().pk) != box_entity for day in days):
        raise ValidationError(words["worker_entity"])
    category = _category(words)
    number = (LabourPaySheet.objects.filter(project=project).order_by("-number").values_list("number", flat=True).first() or 0) + 1
    day = pay_date or timezone.localdate()
    expense = record_expense(category=category, cashbox=cashbox, amount=total, expense_date=day, user=user, payee=CATEGORY_AR,
                             description=f"كشف عمالة {number} — مشروع {project.code} ({period_from.isoformat()} → {period_to.isoformat()})"[:255])
    services.link_expense(project, expense, user, lang=lang, heading=CostHeading.LABOUR)
    sheet = LabourPaySheet.objects.create(project=project, number=number, period_from=period_from, period_to=period_to, total=total,
                                          day_hours=hours, expense=expense, created_by=user)
    LabourDay.objects.filter(pk__in=[d.pk for d in days]).update(pay_sheet=sheet)
    _audit(project, user, "pay_labour_sheet", {"sheet": number, "expense": expense.expense_number, "total": str(total),
                                                "from": period_from.isoformat(), "to": period_to.isoformat(), "days": len(days)},
           AuditEventType.CREATE)
    return sheet


def sheets(project):
    """The project's pay sheets (this entity's cashboxes), newest first, each with whether its expense still stands."""

    queryset = entity_scope.scope(project.labour_sheets.select_related("expense", "expense__cashbox", "expense__cashbox_operation"),
                                  "expense__" + entity_scope.EXPENSE)
    return [{"sheet": sheet, "paid": sheet.expense_id is not None and sheet.expense.cashbox_operation.status != "cancelled"}
            for sheet in queryset]


def sheet_rows(sheet):
    """A paid sheet as it is printed: its days by worker, at the divisor it was paid with. A sheet whose expense was
    cancelled and whose days a later sheet took over shows no days (its stored total stays)."""

    return _group(list(sheet.days.select_related("worker")), sheet.day_hours)


def labour_cost(project):
    """The project's labour cost as its figures read it (posted linked expenses under «labour»)."""

    from .costs import actual_by_heading

    return actual_by_heading(project).get(CostHeading.LABOUR, money_round(ZERO))


def crew_by_trade(project, work_date):
    """The day's recorded workers by trade: [(trade, count)] (for a daily site report; not wired into C2b's form yet)."""

    counts = {}
    for day in days_in_scope(project.labour_days.filter(work_date=work_date)).select_related("worker"):
        trade = day.worker.trade or "—"
        counts[trade] = counts.get(trade, 0) + 1
    return sorted(counts.items())
