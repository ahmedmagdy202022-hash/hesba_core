"""INSTAL-001: create a schedule, collect instalments, and read where each one stands."""

import calendar
from datetime import date
from decimal import ROUND_DOWN, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from sales.models import CustomerPayment, SalesInvoice
from settings_core.capabilities import capability_enabled

from .models import Instalment, InstalmentPayment, InstalmentPlan, PlanStatus


ZERO = Decimal("0.00")
MAX_COUNT = 60
MESSAGES = {
    "ar": {
        "not_posted": "التقسيط على فاتورة مرحّلة بس.", "certificate": "دي فاتورة مستخلص مشروع: تحصيلها بيتسجل من شاشة دفعات المشروع عشان ضمان الأعمال والدفعة المقدمة يتخصموا صح، فمينفعش تتقسط.", "nothing_due": "مفيش مبلغ متبقي على الفاتورة يتقسط.", "exists": "الفاتورة دي عليها خطة تقسيط بالفعل.",
        "count": "عدد الأقساط لازم من 1 لـ {max}.", "first_due": "أول قسط لازم يكون بعد تاريخ الفاتورة.", "amount": "المبلغ لازم أكبر من صفر ومش أكبر من الباقي ({left}).",
        "inactive": "الخطة ملغية أو الفاتورة اتلغت.", "has_payments": "مينفعش تلغي خطة عليها تحصيلات؛ الغي التحصيل الأول من شاشة التحصيلات.",
    },
    "en": {
        "not_posted": "Only a posted invoice can be put on instalments.", "certificate": "This is a project certificate's invoice: its collections are recorded on the project's payments tab so retention and the advance come off correctly, so it cannot be put on instalments.", "nothing_due": "Nothing is left to pay on this invoice.", "exists": "This invoice already has an instalment plan.",
        "count": "The number of instalments must be between 1 and {max}.", "first_due": "The first instalment must fall after the invoice date.", "amount": "The amount must be above zero and at most what is left ({left}).",
        "inactive": "The plan is cancelled or its invoice was cancelled.", "has_payments": "A plan with collections cannot be cancelled; cancel the collection first from the collections screen.",
    },
}


def installments_enabled():
    return capability_enabled("installments")


def add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def split(amount, count):
    """Equal instalments to the piastre; the last one takes the rounding."""

    each = (Decimal(amount) / count).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return [each] * (count - 1) + [money_round(Decimal(amount) - each * (count - 1))]


def _audit(plan, user, action, after, before=None):
    AuditLog.objects.create(
        event_type=AuditEventType.CREATE if action == "create_instalment_plan" else AuditEventType.UPDATE, actor=user, module="installments", action=action,
        object_type="installments.InstalmentPlan", object_id=str(plan.pk), before_data=before or {}, after_data=after,
    )


@transaction.atomic
def create_plan(invoice, count, first_due_date, user, lang="ar", notes=""):
    words = MESSAGES[lang]
    invoice = SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != "posted":
        raise ValidationError(words["not_posted"])
    if InstalmentPlan.objects.filter(invoice=invoice).exists():
        raise ValidationError(words["exists"])
    if hasattr(invoice, "project_certificate"):
        raise ValidationError(words["certificate"])  # HG-038: its deductions and collections live on the project
    # The plan covers what was left on the invoice; returns are credited against it in schedule().
    financed = money_round(invoice.remaining_due)
    if financed - returned_credit(invoice) <= 0:
        raise ValidationError(words["nothing_due"])
    if not 1 <= int(count) <= MAX_COUNT:
        raise ValidationError(words["count"].format(max=MAX_COUNT))
    if first_due_date <= invoice.invoice_date:
        raise ValidationError(words["first_due"])
    plan = InstalmentPlan.objects.create(invoice=invoice, customer=invoice.customer, financed_amount=financed, count=int(count),
                                         first_due_date=first_due_date, notes=(notes or "").strip()[:255], created_by=user)
    for number, amount in enumerate(split(financed, int(count)), start=1):
        Instalment.objects.create(plan=plan, number=number, due_date=add_months(first_due_date, number - 1), amount=amount)
    _audit(plan, user, "create_instalment_plan", {"invoice": invoice.invoice_number, "financed": str(financed), "count": int(count), "first_due_date": first_due_date.isoformat()})
    return plan


def returned_credit(invoice):
    """What posted sales returns took off the customer's due on this invoice."""

    return invoice.returns.filter(status="posted").aggregate(total=Sum("due_amount"))["total"] or ZERO


def paid_amount(plan):
    return plan.payments.filter(payment__status="posted").aggregate(total=Sum("payment__amount"))["total"] or ZERO


def is_active(plan):
    return plan.status == PlanStatus.ACTIVE and plan.invoice.status == "posted"


def schedule(plan, today=None):
    """The instalments with what is settled on each, oldest first, and the plan totals."""

    today = today or timezone.localdate()
    paid = paid_amount(plan)
    credited = returned_credit(plan.invoice)
    left_to_apply = paid + credited
    rows, overdue, next_due = [], ZERO, None
    for instalment in plan.instalments.all():
        settled = min(instalment.amount, max(left_to_apply, ZERO))
        left_to_apply -= settled
        open_amount = money_round(instalment.amount - settled)
        if open_amount == 0:
            state = "paid"
        elif instalment.due_date < today:
            state = "overdue"
            overdue += open_amount
        elif instalment.due_date == today:
            state = "due_today"
        else:
            state = "upcoming"
        if open_amount and next_due is None:
            next_due = instalment
        rows.append({"instalment": instalment, "settled": money_round(settled), "open": open_amount, "state": state, "partial": 0 < settled < instalment.amount})
    remaining = money_round(max(plan.financed_amount - paid - credited, ZERO))
    return {"rows": rows, "paid": money_round(paid), "credited": money_round(credited), "remaining": remaining, "overdue": money_round(overdue),
            "next_due": next_due, "next_open": next((row["open"] for row in rows if row["open"]), ZERO), "done": remaining == 0}


def _payment_number(plan):
    base = f"INS-{plan.invoice.invoice_number}"[:70]
    n = plan.payments.count() + 1
    while CustomerPayment.objects.filter(payment_number=f"{base}-{n}").exists():
        n += 1
    return f"{base}-{n}"


@transaction.atomic
def collect(plan, amount, cashbox, payment_date, user, lang="ar"):
    """Collect money against the plan: an ordinary customer payment, linked here."""

    from sales.services import record_customer_payment

    words = MESSAGES[lang]
    plan = InstalmentPlan.objects.select_for_update().select_related("invoice", "customer").get(pk=plan.pk)
    if not is_active(plan):
        raise ValidationError(words["inactive"])
    left = schedule(plan, payment_date)["remaining"]
    amount = money_round(Decimal(amount))
    if amount <= 0 or amount > left:
        raise ValidationError(words["amount"].format(left=f"{left:,.2f}"))
    payment = record_customer_payment(_payment_number(plan), payment_date, plan.customer, cashbox, amount, user=user,
                                      notes=f"Instalment plan for invoice {plan.invoice.invoice_number}")
    InstalmentPayment.objects.create(plan=plan, payment=payment)
    _audit(plan, user, "collect_instalment", {"payment": payment.payment_number, "amount": str(amount)})
    return payment


@transaction.atomic
def cancel_plan(plan, user, lang="ar"):
    words = MESSAGES[lang]
    plan = InstalmentPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.payments.filter(payment__status="posted").exists():
        raise ValidationError(words["has_payments"])
    plan.status = PlanStatus.CANCELLED
    plan.save(update_fields=["status"])
    _audit(plan, user, "cancel_instalment_plan", {"status": plan.status}, {"status": PlanStatus.ACTIVE})


def overdue_summary(today=None):
    """{"count": instalments overdue, "amount": their open total, "plans": plans with any} for the dashboard."""

    today = today or timezone.localdate()
    result = {"count": 0, "amount": ZERO, "plans": 0}
    if not installments_enabled():
        return result
    plans = InstalmentPlan.objects.filter(status=PlanStatus.ACTIVE, invoice__status="posted", instalments__due_date__lt=today).distinct().select_related("invoice")
    for plan in plans.prefetch_related("instalments"):
        rows = [row for row in schedule(plan, today)["rows"] if row["state"] == "overdue"]
        if rows:
            result["plans"] += 1
            result["count"] += len(rows)
            result["amount"] += sum((row["open"] for row in rows), ZERO)
    result["amount"] = money_round(result["amount"])
    return result
