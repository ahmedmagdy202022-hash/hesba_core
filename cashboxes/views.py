from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from permissions.decorators import require_permission
from permissions.services import user_has_permission
from reports.selectors import cashbox_report

from .models import Cashbox, CashboxMovement


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
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


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
            "purchase_invoice", "sales_invoice", "supplier_payment", "customer_payment"
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
        ),
    )


@require_permission("cashboxes.view_finance")
def movement_list(request):
    queryset = CashboxMovement.objects.select_related(
        "cashbox", "purchase_invoice", "sales_invoice", "supplier_payment", "customer_payment", "created_by"
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

