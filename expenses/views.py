import re
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from cashboxes.models import Cashbox
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .forms import ExpenseCancelForm, ExpenseCategoryForm, ExpenseForm
from .models import Expense, ExpenseCategory
from .services import RECORD_PERMISSION, VIEW_PERMISSION, cancel_expense, record_expense


STRINGS = {
    "ar": {
        "page_title": "المصروفات",
        "title": "المصروفات",
        "intro": "كل جنيه بيخرج على تشغيل المحل: إيجار ومرتبات وكهرباء وغيرها. بيتخصم من الخزنة وبيتطرح من الربح.",
        "new": "تسجيل مصروف",
        "categories": "بنود المصروفات",
        "search": "بحث",
        "filter": "تطبيق",
        "all_categories": "كل البنود",
        "all_cashboxes": "كل الخزن",
        "from": "من",
        "to": "إلى",
        "total": "إجمالي المصروفات",
        "count": "عدد المصروفات",
        "top": "أكبر بند",
        "empty": "مفيش مصروفات مطابقة.",
        "number": "الرقم",
        "date": "التاريخ",
        "category": "البند",
        "cashbox": "الخزنة",
        "payee": "المستفيد",
        "description": "البيان",
        "amount": "المبلغ",
        "status": "الحالة",
        "posted": "مرحّل",
        "cancelled": "ملغي",
        "cancel": "إلغاء",
        "cancel_reason": "سبب الإلغاء",
        "saved": "تم تسجيل المصروف وخصمه من الخزنة.",
        "cancelled_ok": "تم إلغاء المصروف ورجوع المبلغ للخزنة.",
        "back": "العودة للمصروفات",
        "save": "حفظ وخصم من الخزنة",
        "by_category": "المصروفات حسب البند",
        "add_category": "إضافة بند",
        "category_saved": "تم إضافة البند.",
        "active": "نشط",
        "inactive": "موقوف",
        "deactivate": "إيقاف",
        "activate": "تفعيل",
        "category_toggled": "تم تحديث البند.",
        "cash_note": "المبلغ هيتخصم من الخزنة فورًا. لو الخزنة مفيهاش رصيد كفاية المصروف مش هيتسجل.",
    },
    "en": {
        "page_title": "Expenses",
        "title": "Expenses",
        "intro": "Every pound spent running the business: rent, salaries, utilities and more. Paid out of a cashbox and taken off profit.",
        "new": "Record expense",
        "categories": "Expense categories",
        "search": "Search",
        "filter": "Apply",
        "all_categories": "All categories",
        "all_cashboxes": "All cashboxes",
        "from": "From",
        "to": "To",
        "total": "Total expenses",
        "count": "Number of expenses",
        "top": "Largest category",
        "empty": "No matching expenses.",
        "number": "Number",
        "date": "Date",
        "category": "Category",
        "cashbox": "Cashbox",
        "payee": "Paid to",
        "description": "Description",
        "amount": "Amount",
        "status": "Status",
        "posted": "Posted",
        "cancelled": "Cancelled",
        "cancel": "Cancel",
        "cancel_reason": "Cancellation reason",
        "saved": "Expense recorded and paid out of the cashbox.",
        "cancelled_ok": "Expense cancelled; the amount is back in the cashbox.",
        "back": "Back to expenses",
        "save": "Save and pay out",
        "by_category": "Expenses by category",
        "add_category": "Add category",
        "category_saved": "Category added.",
        "active": "Active",
        "inactive": "Inactive",
        "deactivate": "Deactivate",
        "activate": "Activate",
        "category_toggled": "Category updated.",
        "cash_note": "The amount leaves the cashbox immediately. If the cashbox does not hold enough, the expense is refused.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "words": STRINGS[lang],
        "section": "expenses",
        "page_title": STRINGS[lang]["page_title"],
    }
    context.update(extra)
    return context


def _date(raw):
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _errors(exc):
    return "; ".join(getattr(exc, "messages", None) or [str(exc)])


@require_permission(VIEW_PERMISSION)
def expense_list(request):
    lang = _lang(request)
    query = request.GET.get("q", "").strip()
    date_from_raw = request.GET.get("date_from", "").strip()
    date_to_raw = request.GET.get("date_to", "").strip()
    category_id = request.GET.get("category", "").strip()
    cashbox_id = request.GET.get("cashbox", "").strip()
    date_from, date_to = _date(date_from_raw), _date(date_to_raw)

    queryset = Expense.objects.select_related("category", "cashbox", "cashbox_operation", "created_by")
    if query:
        queryset = queryset.filter(
            Q(expense_number__icontains=query) | Q(description__icontains=query) | Q(payee__icontains=query)
        )
    if date_from:
        queryset = queryset.filter(expense_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(expense_date__lte=date_to)
    if category_id.isdigit():
        queryset = queryset.filter(category_id=category_id)
    else:
        category_id = ""
    if cashbox_id.isdigit():
        queryset = queryset.filter(cashbox_id=cashbox_id)
    else:
        cashbox_id = ""

    posted = queryset.filter(cashbox_operation__status="posted")
    total = posted.aggregate(total=Sum("amount"))["total"] or 0
    by_category = [
        {
            "label": (row["category__name_en"] or row["category__name_ar"]) if lang == "en" else row["category__name_ar"],
            "total": row["total"],
            "count": row["count"],
        }
        for row in posted.values("category__name_ar", "category__name_en")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    ]
    page = Paginator(queryset, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "expenses/list.html",
        _context(
            request,
            page=page,
            query=query,
            date_from=date_from_raw,
            date_to=date_to_raw,
            category_id=category_id,
            cashbox_id=cashbox_id,
            categories=ExpenseCategory.objects.all(),
            cashboxes=Cashbox.objects.filter(active=True),
            total=total,
            posted_count=posted.count(),
            by_category=by_category,
            top_category=by_category[0] if by_category else None,
            can_record=user_has_permission(request.user, RECORD_PERMISSION),
        ),
    )


@require_permission(RECORD_PERMISSION)
def expense_create(request):
    lang = _lang(request)
    form = ExpenseForm(request.POST or None, lang=lang)
    if request.method == "POST" and form.is_valid():
        try:
            record_expense(user=request.user, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, _errors(exc))
        else:
            messages.success(request, STRINGS[lang]["saved"])
            return redirect(f"/expenses/?lang={lang}")
    return render(request, "expenses/form.html", _context(request, form=form, title=STRINGS[lang]["new"]))


@require_permission(RECORD_PERMISSION)
def expense_cancel(request, pk):
    lang = _lang(request)
    if request.method != "POST":
        return redirect(f"/expenses/?lang={lang}")
    get_object_or_404(Expense, pk=pk)
    form = ExpenseCancelForm(request.POST)
    if form.is_valid():
        try:
            cancel_expense(pk, reversal_date=timezone.localdate(), reason=form.cleaned_data["reason"], user=request.user)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _errors(exc))
        else:
            messages.success(request, STRINGS[lang]["cancelled_ok"])
    else:
        messages.error(request, STRINGS[lang]["cancel_reason"])
    return redirect(f"/expenses/?lang={lang}")


def _category_code(name):
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "category"
    code, counter = base[:30], 1
    while ExpenseCategory.objects.filter(code=code).exists():
        counter += 1
        code = f"{base[:26]}_{counter}"
    return code


@require_permission(VIEW_PERMISSION)
def category_list(request):
    lang = _lang(request)
    can_record = user_has_permission(request.user, RECORD_PERMISSION)
    form = ExpenseCategoryForm(request.POST or None, lang=lang)
    if request.method == "POST":
        if not can_record:
            raise PermissionDenied(f"Managing categories needs {RECORD_PERMISSION}.")
        if form.is_valid():
            name_ar = form.cleaned_data["name_ar"]
            name_en = form.cleaned_data["name_en"].strip()
            ExpenseCategory.objects.create(code=_category_code(name_en or f"custom {ExpenseCategory.objects.count() + 1}"), name_ar=name_ar, name_en=name_en)
            messages.success(request, STRINGS[lang]["category_saved"])
            return redirect(f"/expenses/categories/?lang={lang}")
    rows = ExpenseCategory.objects.all()
    return render(
        request,
        "expenses/categories.html",
        _context(request, rows=rows, form=form, can_record=can_record, title=STRINGS[lang]["categories"]),
    )


@require_permission(RECORD_PERMISSION)
def category_toggle(request, pk):
    lang = _lang(request)
    if request.method == "POST":
        category = get_object_or_404(ExpenseCategory, pk=pk)
        category.active = not category.active
        category.save(update_fields=["active"])
        messages.success(request, STRINGS[lang]["category_toggled"])
    return redirect(f"/expenses/categories/?lang={lang}")
