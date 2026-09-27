"""EXP-001: operating expenses, posted through the existing cash-operation service.

An expense never touches a cashbox balance itself. It asks
``create_cashbox_operation`` for a direct cash-out, so the negative-balance
guard, the closed-period guard, row locking and the append-only reversal all
stay the cashbox module's own, unchanged logic (HG-009).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Sum

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxOperationStatus, CashboxOperationType
from cashboxes.services import cancel_cashbox_operation, create_cashbox_operation
from config.money import money_round
from permissions.services import user_has_permission

from .models import Expense, ExpenseCategory


RECORD_PERMISSION = "cashboxes.record_expenses"
VIEW_PERMISSION = "cashboxes.view_expenses"


def _require_record_permission(user):
    if not user_has_permission(user, RECORD_PERMISSION):
        raise PermissionDenied(f"Expenses require {RECORD_PERMISSION}.")


def _next_expense_number():
    last = Expense.objects.select_for_update().order_by("-id").first()
    sequence = (last.id if last else 0) + 1
    number = f"EXP-{sequence:06d}"
    while Expense.objects.filter(expense_number=number).exists():
        sequence += 1
        number = f"EXP-{sequence:06d}"
    return number


def _audit(expense, user, action, reason):
    AuditLog.objects.create(
        event_type=AuditEventType.CREATE if action == "record_expense" else AuditEventType.ADJUSTMENT,
        actor=user,
        module="expenses",
        action=action,
        object_type="Expense",
        object_id=str(expense.pk),
        reason=reason,
        after_data={
            "expense_number": expense.expense_number,
            "category": expense.category.code,
            "cashbox_id": expense.cashbox_id,
            "amount": str(expense.amount),
            "status": expense.cashbox_operation.status,
        },
    )


@transaction.atomic
def record_expense(*, category, cashbox, amount, expense_date, description, user, payee=""):
    """Pay an expense out of ``cashbox``. Returns the Expense."""

    _require_record_permission(user)
    description = (description or "").strip()
    if not description:
        raise ValidationError("An expense needs a description.")
    if category is None or not category.active:
        raise ValidationError("Choose an active expense category.")
    amount = money_round(amount)
    if amount <= 0:
        raise ValidationError("Expense amount must be greater than zero.")

    number = _next_expense_number()
    operation = create_cashbox_operation(
        reference_number=number,
        operation_date=expense_date,
        operation_type=CashboxOperationType.DIRECT_OUT,
        amount=amount,
        reason=f"{category.name_ar}: {description}",
        user=user,
        source_cashbox=cashbox,
    )
    expense = Expense.objects.create(
        expense_number=number,
        expense_date=expense_date,
        category=category,
        cashbox=operation.source_cashbox,
        amount=operation.amount,
        payee=(payee or "").strip(),
        description=description,
        cashbox_operation=operation,
        created_by=user,
    )
    _audit(expense, user, "record_expense", description)
    return expense


@transaction.atomic
def cancel_expense(expense_id, *, reversal_date, reason, user):
    """Cancel an expense by reversing its cash operation (money back to the cashbox)."""

    _require_record_permission(user)
    expense = Expense.objects.select_for_update().select_related("cashbox_operation", "category").get(pk=expense_id)
    if expense.cashbox_operation.status != CashboxOperationStatus.POSTED:
        raise ValidationError("This expense is already cancelled.")
    cancel_cashbox_operation(expense.cashbox_operation_id, reversal_date, reason, user)
    expense.cashbox_operation.refresh_from_db()
    _audit(expense, user, "cancel_expense", reason)
    return expense


def posted_expenses(date_from=None, date_to=None):
    queryset = Expense.objects.filter(cashbox_operation__status=CashboxOperationStatus.POSTED)
    if date_from:
        queryset = queryset.filter(expense_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(expense_date__lte=date_to)
    return queryset


def expense_total(date_from=None, date_to=None):
    """Posted expenses in the window. Cancelled ones count as never spent."""

    total = posted_expenses(date_from, date_to).aggregate(total=Sum("amount"))["total"]
    return money_round(total or 0)


def expense_totals_by_category(date_from=None, date_to=None, lang="ar"):
    rows = (
        posted_expenses(date_from, date_to)
        .values("category_id", "category__name_ar", "category__name_en")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    return [
        {
            "category_id": row["category_id"],
            "label": (row["category__name_en"] or row["category__name_ar"]) if lang == "en" else row["category__name_ar"],
            "total": money_round(row["total"]),
            "count": row["count"],
        }
        for row in rows
    ]


def active_categories():
    return ExpenseCategory.objects.filter(active=True)
