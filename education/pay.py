"""EDU-004: what each teacher is owed for a month, and paying it as an ordinary expense.

A group says how its teacher is paid: an amount per class held, a share of the
fees the group billed that month, or a fixed amount per month. A payment goes
through the expenses engine unchanged (``expenses.services.record_expense``),
so the cash leaves the chosen cashbox, it shows among the expenses and comes
off profit like any salary. Cancelling that expense on the expenses page puts
the cash back and the month is owed again.
"""

from collections import OrderedDict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from . import services
from .billing import _live, month_end, month_label
from .models import ClassSession, Enrollment, FeeCharge, SessionStatus, StudyGroup, TeacherPay, TeacherPayment

ZERO = Decimal("0")
PAY_VIEW = "cashboxes.view_expenses"
PAY_RECORD = "cashboxes.record_expenses"
CATEGORY_CODE = "TEACHER-PAY"

MESSAGES = {
    "ar": {"future": "مينفعش تدفع عن شهر لسه مجاش.", "amount": "المبلغ لازم يكون أكبر من صفر.",
           "too_much": "المبلغ أكبر من الباقي للمدرس في الشهر ده ({remaining}).", "cashbox": "اختار الخزنة اللي هيتصرف منها.",
           "category": "بند «أجور المدرسين» في المصروفات متوقف. فعّله من إعدادات المصروفات الأول.",
           "description": "أجر {teacher} — {month}", "teacher": "اختار المدرس.",
           "entity": "اختار الكيان (الفرع) الأول من أعلى الشاشة؛ أجور كل كيان بتتدفع من خزنته."},
    "en": {"future": "A month still to come cannot be paid.", "amount": "The amount must be more than zero.",
           "too_much": "The amount is more than the teacher is still owed for this month ({remaining}).", "cashbox": "Choose the cashbox to pay from.",
           "category": "The “Teacher pay” expense category is switched off. Turn it on in the expense settings first.",
           "description": "Pay for {teacher} — {month}", "teacher": "Choose the teacher.",
           "entity": "Choose the entity (branch) first, at the top of the screen; each entity pays its teachers from its own cashbox."},
}


def _runs_in(group, first, last):
    """The group's own term touches the month."""

    return not (group.starts_on and group.starts_on > last) and not (group.ends_on and group.ends_on < first)


def group_due(group, first):
    """What the group owes its teacher for the month: {"units", "due"} (units: classes held, fees billed, or 1 month)."""

    last = month_end(first)
    rate = group.teacher_rate or ZERO
    if group.teacher_id is None or group.teacher_pay == TeacherPay.NONE or not _runs_in(group, first, last):
        return {"units": ZERO, "due": ZERO}
    if group.teacher_pay == TeacherPay.SESSION:
        held = ClassSession.objects.filter(group=group, status=SessionStatus.HELD, date__gte=first, date__lte=last).count()
        return {"units": Decimal(held), "due": money_round(held * rate)}
    if group.teacher_pay == TeacherPay.PERCENT:
        fees = _live(FeeCharge.objects.filter(enrollment__group=group, month=first)).aggregate(total=Sum("amount"))["total"] or ZERO
        return {"units": fees, "due": money_round(fees * rate / Decimal("100"))}
    # A fixed month is owed when the group had students in it.
    taught = Enrollment.objects.filter(group=group, start_date__lte=last).filter(Q(end_date__isnull=True) | Q(end_date__gte=first)).exists()
    return {"units": Decimal("1") if taught else ZERO, "due": money_round(rate) if taught else ZERO}


def _paid(teacher, first):
    payments = services.scoped(TeacherPayment.objects).filter(teacher=teacher, month=first, expense__cashbox_operation__status="posted")
    return payments.aggregate(total=Sum("amount"))["total"] or ZERO


def teacher_rows(first):
    """Per teacher for the month: [{teacher, groups: [{group, units, due}], due, paid, remaining}], by name."""

    groups = (services.scoped(StudyGroup.objects).filter(teacher__isnull=False).exclude(teacher_pay=TeacherPay.NONE)
              .select_related("teacher", "course").order_by("teacher__name", "course__name", "name"))
    rows = OrderedDict()
    for group in groups:
        line = group_due(group, first)
        row = rows.setdefault(group.teacher_id, {"teacher": group.teacher, "groups": [], "due": ZERO})
        row["groups"].append({"group": group, **line})
        row["due"] += line["due"]
    for row in rows.values():
        row["paid"] = _paid(row["teacher"], first)
        row["remaining"] = max(row["due"] - row["paid"], ZERO)
    return list(rows.values())


def owed(teacher, first):
    row = next((row for row in teacher_rows(first) if row["teacher"].pk == teacher.pk), None)
    return row["remaining"] if row else ZERO


def _category(words):
    from expenses.models import ExpenseCategory

    category, _ = ExpenseCategory.objects.get_or_create(code=CATEGORY_CODE, defaults={"name_ar": "أجور المدرسين", "name_en": "Teacher pay"})
    if not category.active:
        raise ValidationError(words["category"])
    return category


@transaction.atomic
def pay(teacher, first, amount, cashbox, user, *, pay_date=None, lang="ar"):
    """Pay ``teacher`` for the month out of ``cashbox``. Returns the TeacherPayment."""

    from expenses.services import record_expense
    from staff.models import Employee

    from entities.current import current_entity
    from entities.services import is_multi_entity

    words = MESSAGES[lang]
    if current_entity() is None and is_multi_entity():
        raise ValidationError(words["entity"])  # as with billing: the whole group at once would mix the branches' pay
    if teacher is None:
        raise ValidationError(words["teacher"])
    if cashbox is None:
        raise ValidationError(words["cashbox"])
    if first > timezone.localdate().replace(day=1):
        raise ValidationError(words["future"])
    Employee.objects.select_for_update().filter(pk=teacher.pk).exists()  # two payments at once never pay one month twice
    amount = services._money(amount, services.MESSAGES[lang])
    if amount is None or amount <= 0:
        raise ValidationError(words["amount"])
    remaining = owed(teacher, first)
    if amount > remaining:
        from settings_core.templatetags.hesba_format import money

        raise ValidationError(words["too_much"].format(remaining=money(remaining)))
    label = month_label(first, lang)
    expense = record_expense(category=_category(words), cashbox=cashbox, amount=amount,
                             expense_date=pay_date or timezone.localdate(),  # the cash leaves on the day it is paid
                             description=words["description"].format(teacher=teacher.name, month=label), user=user, payee=teacher.name)
    payment = TeacherPayment.objects.create(teacher=teacher, month=first, amount=expense.amount, expense=expense, entity=services.owner_entity(), created_by=user)
    AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=user, module="education", action="pay_teacher", object_type="education.TeacherPayment",
                            object_id=str(payment.pk), before_data={},
                            after_data={"teacher": teacher.code, "month": first.isoformat(), "amount": str(expense.amount), "expense": expense.expense_number})
    return payment
