from django.core.paginator import Paginator
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from permissions.decorators import require_permission
from permissions.services import user_has_permission
from reports.selectors import cashbox_report

from .forms import CashboxOperationForm, CashboxOperationReversalForm
from .models import Cashbox, CashboxMovement, CashboxOperation
from .services import cancel_cashbox_operation, create_cashbox_operation


STRINGS = {
    "ar": {
        "page_title": "الخزن",
        "cashboxes": "الخزن",
        "movements": "سجل حركات الخزن",
        "search": "بحث",
        "all": "الكل",
        "empty": "لا توجد نتائج مطابقة.",
        "dashboard": "لوحة القيادة",
        "language": "English",
        "back": "العودة للخزن",
        "finance_hidden": "التفاصيل المالية وحركات الخزنة تحتاج صلاحية عرض مالية مستقلة.",
        "blocked": "الإدخال النقدي المباشر والتحويل بين الخزن غير متاحين حتى اعتماد خدمة الحركة المحمية.",
        "operations": "حركات النقد المباشرة والتحويلات",
        "new_operation": "تسجيل حركة نقدية",
        "saved": "تم ترحيل حركة النقد.",
        "reversed": "تم عكس حركة النقد.",
    },
    "en": {
        "page_title": "Cashboxes",
        "cashboxes": "Cashboxes",
        "movements": "Cashbox movement history",
        "search": "Search",
        "all": "All",
        "empty": "No matching results.",
        "dashboard": "Dashboard",
        "language": "العربية",
        "back": "Back to cashboxes",
        "finance_hidden": "Financial details and cashbox movements require separate finance-view permission.",
        "blocked": "Direct cash movements and transfers remain unavailable until a protected movement service is approved.",
        "operations": "Direct cash and transfers",
        "new_operation": "Record cash operation",
        "saved": "Cash operation posted.",
        "reversed": "Cash operation reversed.",
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
        "section": "cashboxes",
        "page_title": STRINGS[lang]["page_title"],
    }
    context.update(extra)
    return context


@require_permission("cashboxes.view_cashboxes")
def cashbox_list(request):
    query = request.GET.get("q", "").strip()
    queryset = Cashbox.objects.filter(active=True)
    if query:
        queryset = queryset.filter(
            Q(cashbox_code__icontains=query)
            | Q(name_ar__icontains=query)
            | Q(name_en__icontains=query)
        )
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    can_view_finance = user_has_permission(request.user, "cashboxes.view_finance")
    finance_by_id = {}
    if can_view_finance:
        finance_by_id = {row["cashbox_id"]: row for row in cashbox_report()}
    rows = [
        {"cashbox": cashbox, "finance": finance_by_id.get(cashbox.id)}
        for cashbox in page.object_list
    ]
    return render(
        request,
        "cashboxes/list.html",
        _context(
            request,
            page=page,
            rows=rows,
            query=query,
            can_view_finance=can_view_finance,
            can_move_cash=user_has_permission(request.user, "cashboxes.move_cash"),
        ),
    )


@require_permission("cashboxes.view_cashboxes")
def cashbox_detail(request, pk):
    cashbox = get_object_or_404(Cashbox, pk=pk, active=True)
    can_view_finance = user_has_permission(request.user, "cashboxes.view_finance")
    finance = None
    movements = None
    if can_view_finance:
        rows = cashbox_report(cashbox=cashbox)
        finance = rows[0] if rows else None
        movements = cashbox.movements.select_related(
            "purchase_invoice", "sales_invoice", "supplier_payment", "customer_payment",
            "cashbox_operation",
        )[:100]
    return render(
        request,
        "cashboxes/detail.html",
        _context(
            request,
            cashbox=cashbox,
            finance=finance,
            movements=movements,
            can_view_finance=can_view_finance,
            can_move_cash=user_has_permission(request.user, "cashboxes.move_cash"),
            can_adjust_opening=(
                cashbox.movements.exists()
                and user_has_permission(request.user, "master_data.adjust_opening_balances")
            ),
        ),
    )


@require_permission("cashboxes.view_finance")
def movement_list(request):
    queryset = CashboxMovement.objects.select_related(
        "cashbox", "purchase_invoice", "sales_invoice", "supplier_payment", "customer_payment",
        "cashbox_operation", "created_by"
    )
    query = request.GET.get("q", "").strip()
    cashbox_id = request.GET.get("cashbox", "").strip()
    movement_type = request.GET.get("type", "").strip()
    if query:
        queryset = queryset.filter(
            Q(description__icontains=query)
            | Q(purchase_invoice__invoice_number__icontains=query)
            | Q(sales_invoice__invoice_number__icontains=query)
            | Q(supplier_payment__payment_number__icontains=query)
            | Q(customer_payment__payment_number__icontains=query)
            | Q(cashbox_operation__reference_number__icontains=query)
        )
    if cashbox_id.isdigit():
        queryset = queryset.filter(cashbox_id=cashbox_id)
    else:
        cashbox_id = ""
    valid_types = {value for value, _ in CashboxMovement._meta.get_field("movement_type").choices}
    if movement_type in valid_types:
        queryset = queryset.filter(movement_type=movement_type)
    else:
        movement_type = ""
    page = Paginator(queryset, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "cashboxes/movements.html",
        _context(
            request,
            page=page,
            query=query,
            cashbox_id=cashbox_id,
            movement_type=movement_type,
            cashboxes=Cashbox.objects.filter(active=True),
            movement_choices=CashboxMovement._meta.get_field("movement_type").choices,
        ),
    )


@require_permission("cashboxes.move_cash")
def operation_list(request):
    page = Paginator(
        CashboxOperation.objects.select_related(
            "source_cashbox", "destination_cashbox", "created_by", "cancelled_by"
        ),
        50,
    ).get_page(request.GET.get("page"))
    return render(
        request,
        "cashboxes/operations.html",
        _context(
            request,
            page=page,
            reversal_form=CashboxOperationReversalForm(lang=_lang(request)),
        ),
    )


@require_permission("cashboxes.move_cash")
def operation_create(request):
    lang = _lang(request)
    form = CashboxOperationForm(request.POST or None, lang=lang)
    if request.method == "POST" and form.is_valid():
        try:
            create_cashbox_operation(user=request.user, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, STRINGS[lang]["saved"])
            return redirect(f"/cashboxes/operations/?lang={lang}")
    return render(
        request,
        "cashboxes/operation_form.html",
        _context(request, form=form, title=STRINGS[lang]["new_operation"]),
    )


@require_permission("cashboxes.move_cash")
def operation_cancel(request, pk):
    if request.method != "POST":
        return redirect("cashboxes:operations")
    lang = _lang(request)
    form = CashboxOperationReversalForm(request.POST, lang=lang)
    if form.is_valid():
        try:
            cancel_cashbox_operation(pk, user=request.user, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, STRINGS[lang]["reversed"])
    else:
        messages.error(request, "; ".join(error for errors in form.errors.values() for error in errors))
    return redirect(f"/cashboxes/operations/?lang={lang}")
