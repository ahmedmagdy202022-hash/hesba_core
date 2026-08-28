from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .forms import CustomerPaymentForm, SalesDraftForm, SalesLineFormSet
from .models import CustomerPayment, SalesInvoice
from .services import (
    cancel_customer_payment,
    cancel_posted_sales_invoice,
    create_sales_draft,
    post_sales_invoice,
    record_customer_payment,
)


STRINGS = {
    "ar": {
        "page_title": "المبيعات",
        "invoices": "فواتير البيع",
        "new": "فاتورة بيع جديدة",
        "search": "بحث",
        "empty": "لا توجد فواتير بيع مطابقة.",
        "save_draft": "حفظ المسودة",
        "lines": "بنود الفاتورة",
        "post": "ترحيل الفاتورة",
        "cancel": "إلغاء وعكس الفاتورة",
        "back": "العودة للمبيعات",
        "saved": "تم حفظ مسودة فاتورة البيع.",
        "posted": "تم ترحيل فاتورة البيع.",
        "cancelled": "تم إلغاء فاتورة البيع وعكس آثارها.",
        "language": "English",
        "dashboard": "لوحة القيادة",
        "all": "كل الحالات",
        "cost_warning": "تكلفة البيع تستخدم متوسط التكلفة المخزن حاليًا؛ راجع HG-003 قبل اعتماد الربح للإصدار.",
        "collections": "تحصيلات العملاء",
        "new_collection": "تحصيل جديد من عميل",
        "collection_saved": "تم تسجيل تحصيل العميل.",
        "collection_cancelled": "تم إلغاء تحصيل العميل وعكس آثاره.",
    },
    "en": {
        "page_title": "Sales",
        "invoices": "Sales invoices",
        "new": "New sales invoice",
        "search": "Search",
        "empty": "No matching sales invoices.",
        "save_draft": "Save draft",
        "lines": "Invoice lines",
        "post": "Post invoice",
        "cancel": "Cancel and reverse invoice",
        "back": "Back to sales",
        "saved": "Sales draft saved.",
        "posted": "Sales invoice posted.",
        "cancelled": "Sales invoice cancelled and reversed.",
        "language": "العربية",
        "dashboard": "Dashboard",
        "all": "All statuses",
        "cost_warning": "Sales costing uses the currently stored average cost; review HG-003 before profit is release-ready.",
        "collections": "Customer collections",
        "new_collection": "New customer collection",
        "collection_saved": "Customer collection recorded.",
        "collection_cancelled": "Customer collection cancelled and reversed.",
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
        "section": "sales",
        "page_title": STRINGS[lang]["page_title"],
    }
    context.update(extra)
    return context


@require_permission("sales.view_sales_invoices")
def invoice_list(request):
    queryset = SalesInvoice.objects.select_related("customer", "selling_location", "cashbox")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if query:
        queryset = queryset.filter(
            Q(invoice_number__icontains=query) | Q(customer__name__icontains=query)
        )
    if status in {"draft", "posted", "cancelled"}:
        queryset = queryset.filter(status=status)
    else:
        status = ""
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(request, "sales/list.html", _context(request, page=page, query=query, status_filter=status))


@require_permission("sales.create_sales_invoice")
def invoice_create(request):
    lang = _lang(request)
    if request.method == "POST":
        form = SalesDraftForm(request.POST, lang=lang)
        line_formset = SalesLineFormSet(request.POST, prefix="lines", form_kwargs={"lang": lang})
        if form.is_valid() and line_formset.is_valid():
            line_data = [row.cleaned_data for row in line_formset.forms if row.cleaned_data.get("item") is not None]
            try:
                invoice = create_sales_draft(form.cleaned_data, line_data, request.user)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, STRINGS[lang]["saved"])
                return redirect(f"/sales/{invoice.pk}/?lang={lang}")
    else:
        form = SalesDraftForm(lang=lang)
        line_formset = SalesLineFormSet(prefix="lines", form_kwargs={"lang": lang})
    return render(request, "sales/form.html", _context(request, form=form, line_formset=line_formset))


@require_permission("sales.view_sales_invoices")
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        SalesInvoice.objects.select_related("customer", "selling_location", "cashbox").prefetch_related("lines__item"),
        pk=pk,
    )
    return render(
        request,
        "sales/detail.html",
        _context(
            request,
            invoice=invoice,
            can_view_cost=user_has_permission(request.user, "inventory.view_cost"),
            can_view_profit=user_has_permission(request.user, "reports.view_profit_report"),
        ),
    )


@require_permission("sales.create_sales_invoice")
def invoice_post(request, pk):
    if request.method != "POST":
        return redirect("sales:detail", pk=pk)
    lang = _lang(request)
    try:
        post_sales_invoice(pk, request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["posted"])
    return redirect(f"/sales/{pk}/?lang={lang}")


@require_permission("sales.return_sale")
def invoice_cancel(request, pk):
    if request.method != "POST":
        return redirect("sales:detail", pk=pk)
    lang = _lang(request)
    try:
        cancel_posted_sales_invoice(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["cancelled"])
    return redirect(f"/sales/{pk}/?lang={lang}")


@require_permission("sales.receive_customer_payment")
def payment_list(request):
    queryset = CustomerPayment.objects.select_related("customer", "cashbox", "created_by")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if query:
        queryset = queryset.filter(
            Q(payment_number__icontains=query) | Q(customer__name__icontains=query)
        )
    if status in {"posted", "cancelled"}:
        queryset = queryset.filter(status=status)
    else:
        status = ""
    lang = _lang(request)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "payments/list.html",
        _context(
            request,
            page=page,
            query=query,
            status_filter=status,
            title=STRINGS[lang]["collections"],
            new_label=STRINGS[lang]["new_collection"],
            create_url="sales:payment_create",
            cancel_url="sales:payment_cancel",
            party_kind="customer",
        ),
    )


@require_permission("sales.receive_customer_payment")
def payment_create(request):
    lang = _lang(request)
    if request.method == "POST":
        form = CustomerPaymentForm(request.POST, lang=lang)
        if form.is_valid():
            try:
                record_customer_payment(user=request.user, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, STRINGS[lang]["collection_saved"])
                return redirect(f"/sales/collections/?lang={lang}")
    else:
        form = CustomerPaymentForm(lang=lang)
    return render(
        request,
        "payments/form.html",
        _context(
            request,
            form=form,
            title=STRINGS[lang]["new_collection"],
            back_url="sales:payments",
        ),
    )


@require_permission("sales.receive_customer_payment")
def payment_cancel(request, pk):
    if request.method != "POST":
        return redirect("sales:payments")
    lang = _lang(request)
    try:
        cancel_customer_payment(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["collection_cancelled"])
    return redirect(f"/sales/collections/?lang={lang}")
