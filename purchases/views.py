from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from permissions.decorators import require_permission

from .forms import PurchaseDraftForm, PurchaseLineFormSet, SupplierPaymentForm
from .models import PurchaseInvoice, SupplierPayment
from .services import (
    cancel_posted_purchase_invoice,
    cancel_supplier_payment,
    create_purchase_draft,
    post_purchase_invoice,
    record_supplier_payment,
)


STRINGS = {
    "ar": {
        "page_title": "المشتريات",
        "invoices": "فواتير الشراء",
        "new": "فاتورة شراء جديدة",
        "search": "بحث",
        "empty": "لا توجد فواتير شراء مطابقة.",
        "save_draft": "حفظ المسودة",
        "lines": "بنود الفاتورة",
        "post": "ترحيل الفاتورة",
        "cancel": "إلغاء وعكس الفاتورة",
        "back": "العودة للمشتريات",
        "saved": "تم حفظ مسودة فاتورة الشراء.",
        "posted": "تم ترحيل فاتورة الشراء.",
        "cancelled": "تم إلغاء فاتورة الشراء وعكس آثارها.",
        "language": "English",
        "dashboard": "لوحة القيادة",
        "all": "كل الحالات",
        "payments": "مدفوعات الموردين",
        "new_payment": "سداد جديد لمورد",
        "payment_saved": "تم تسجيل سداد المورد.",
        "payment_cancelled": "تم إلغاء سداد المورد وعكس آثاره.",
    },
    "en": {
        "page_title": "Purchases",
        "invoices": "Purchase invoices",
        "new": "New purchase invoice",
        "search": "Search",
        "empty": "No matching purchase invoices.",
        "save_draft": "Save draft",
        "lines": "Invoice lines",
        "post": "Post invoice",
        "cancel": "Cancel and reverse invoice",
        "back": "Back to purchases",
        "saved": "Purchase draft saved.",
        "posted": "Purchase invoice posted.",
        "cancelled": "Purchase invoice cancelled and reversed.",
        "language": "العربية",
        "dashboard": "Dashboard",
        "all": "All statuses",
        "payments": "Supplier payments",
        "new_payment": "New supplier payment",
        "payment_saved": "Supplier payment recorded.",
        "payment_cancelled": "Supplier payment cancelled and reversed.",
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
        "section": "purchases",
        "page_title": STRINGS[lang]["page_title"],
    }
    context.update(extra)
    return context


@require_permission("purchases.view_purchase_invoices")
def invoice_list(request):
    queryset = PurchaseInvoice.objects.select_related(
        "supplier", "receiving_location", "cashbox"
    )
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if query:
        queryset = queryset.filter(
            Q(invoice_number__icontains=query) | Q(supplier__name__icontains=query)
        )
    if status in {"draft", "posted", "cancelled"}:
        queryset = queryset.filter(status=status)
    else:
        status = ""
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "purchases/list.html",
        _context(request, page=page, query=query, status_filter=status),
    )


@require_permission("purchases.create_purchase_invoice")
def invoice_create(request):
    lang = _lang(request)
    if request.method == "POST":
        form = PurchaseDraftForm(request.POST, lang=lang)
        line_formset = PurchaseLineFormSet(request.POST, prefix="lines", form_kwargs={"lang": lang})
        if form.is_valid() and line_formset.is_valid():
            line_data = [
                row.cleaned_data
                for row in line_formset.forms
                if row.cleaned_data.get("item") is not None
            ]
            try:
                invoice = create_purchase_draft(form.cleaned_data, line_data, request.user)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, STRINGS[lang]["saved"])
                return redirect(f"/purchases/{invoice.pk}/?lang={lang}")
    else:
        form = PurchaseDraftForm(lang=lang)
        line_formset = PurchaseLineFormSet(prefix="lines", form_kwargs={"lang": lang})
    return render(
        request,
        "purchases/form.html",
        _context(request, form=form, line_formset=line_formset),
    )


@require_permission("purchases.view_purchase_invoices")
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        PurchaseInvoice.objects.select_related("supplier", "receiving_location", "cashbox").prefetch_related("lines__item"),
        pk=pk,
    )
    return render(request, "purchases/detail.html", _context(request, invoice=invoice))


@require_permission("purchases.create_purchase_invoice")
def invoice_post(request, pk):
    if request.method != "POST":
        return redirect("purchases:detail", pk=pk)
    lang = _lang(request)
    try:
        post_purchase_invoice(pk, request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["posted"])
    return redirect(f"/purchases/{pk}/?lang={lang}")


@require_permission("purchases.return_purchase")
def invoice_cancel(request, pk):
    if request.method != "POST":
        return redirect("purchases:detail", pk=pk)
    lang = _lang(request)
    try:
        cancel_posted_purchase_invoice(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["cancelled"])
    return redirect(f"/purchases/{pk}/?lang={lang}")


@require_permission("purchases.pay_supplier")
def payment_list(request):
    queryset = SupplierPayment.objects.select_related("supplier", "cashbox", "created_by")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if query:
        queryset = queryset.filter(
            Q(payment_number__icontains=query) | Q(supplier__name__icontains=query)
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
            title=STRINGS[lang]["payments"],
            new_label=STRINGS[lang]["new_payment"],
            create_url="purchases:payment_create",
            cancel_url="purchases:payment_cancel",
            party_kind="supplier",
        ),
    )


@require_permission("purchases.pay_supplier")
def payment_create(request):
    lang = _lang(request)
    if request.method == "POST":
        form = SupplierPaymentForm(request.POST, lang=lang)
        if form.is_valid():
            try:
                record_supplier_payment(user=request.user, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, STRINGS[lang]["payment_saved"])
                return redirect(f"/purchases/payments/?lang={lang}")
    else:
        form = SupplierPaymentForm(lang=lang)
    return render(
        request,
        "payments/form.html",
        _context(
            request,
            form=form,
            title=STRINGS[lang]["new_payment"],
            back_url="purchases:payments",
        ),
    )


@require_permission("purchases.pay_supplier")
def payment_cancel(request, pk):
    if request.method != "POST":
        return redirect("purchases:payments")
    lang = _lang(request)
    try:
        cancel_supplier_payment(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["payment_cancelled"])
    return redirect(f"/purchases/payments/?lang={lang}")
