from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from barcode.services import item_catalog, scan_words
from taxes.services import create_purchase_draft_with_tax, rates_by_item, vat_enabled
from batches.services import attach_purchase_batches
from serials.services import attach_purchase_serials, prepare_purchase_serials
from units.services import convert_lines, units_catalog
from permissions.decorators import require_permission
from entities import scope as entity_scope
from permissions.services import user_has_permission

from .forms import (
    PurchaseDraftForm,
    PurchaseLineFormSet,
    PurchaseReturnForm,
    PurchaseReturnLineFormSet,
    PurchaseReturnReversalForm,
    SupplierPaymentForm,
)
from .models import PurchaseInvoice, PurchaseReturn, SupplierPayment
from .services import (
    cancel_posted_purchase_invoice,
    cancel_purchase_return,
    cancel_supplier_payment,
    create_purchase_return,
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
        "new_return": "مرتجع شراء جديد",
        "return_saved": "تم ترحيل مرتجع الشراء.",
        "return_reversed": "تم عكس مرتجع الشراء.",
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
        "new_return": "New purchase return",
        "return_saved": "Purchase return posted.",
        "return_reversed": "Purchase return reversed.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _party_initial(request, field):
    """PARTY-001: "New invoice" / "Collect" from a party card pre-selects that party."""

    value = request.GET.get(field) or ""
    return {field: value} if value.isdigit() else None


def _reorder_initial(request):
    """REORDER-001: lines suggested by the "buy soon" screen, for the owner to review."""

    from decimal import Decimal, InvalidOperation

    from master_data.models import Item

    items = request.GET.getlist("reorder_item")[:15]
    quantities, prices = request.GET.getlist("reorder_qty"), request.GET.getlist("reorder_price")
    found = {str(item.pk): item for item in Item.objects.filter(pk__in=[pk for pk in items if pk.isdigit()], active=True)}
    lines = []
    for index, pk in enumerate(items):
        item = found.get(pk)
        try:
            quantity = Decimal(quantities[index])
            price = Decimal(prices[index])
        except (IndexError, InvalidOperation):
            continue
        if item is not None and quantity > 0 and price >= 0:
            lines.append({"item": item.pk, "quantity": quantity, "unit_purchase_price": price, "line_discount_amount": Decimal("0")})
    return lines or None


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
    queryset = entity_scope.scope(PurchaseInvoice.objects.select_related(
        "supplier", "receiving_location", "cashbox"
    ), entity_scope.PURCHASE_INVOICE)
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
        if vat_enabled():
            del form.fields["tax_amount"]  # TAX-002: input VAT per line from item rates
        line_formset = PurchaseLineFormSet(request.POST, prefix="lines", form_kwargs={"lang": lang})
        if form.is_valid() and line_formset.is_valid():
            line_data = [
                row.cleaned_data
                for row in line_formset.forms
                if row.cleaned_data.get("item") is not None
            ]
            try:
                line_data = convert_lines(line_data, "unit_purchase_price", lang)  # UNITS-001
                line_data = prepare_purchase_serials(line_data, lang)  # SERIAL-001
                with transaction.atomic():
                    invoice = create_purchase_draft_with_tax(form.cleaned_data, line_data, request.user)
                    attach_purchase_batches(invoice, line_data, request.user)  # BATCH-001
                    attach_purchase_serials(invoice, line_data, request.user)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, STRINGS[lang]["saved"])
                return redirect(f"/purchases/{invoice.pk}/?lang={lang}")
    else:
        form = PurchaseDraftForm(lang=lang, initial=_party_initial(request, "supplier"))
        if vat_enabled():
            del form.fields["tax_amount"]
        line_formset = PurchaseLineFormSet(prefix="lines", form_kwargs={"lang": lang}, initial=_reorder_initial(request))
    return render(
        request,
        "purchases/form.html",
        _context(request, form=form, line_formset=line_formset, item_catalog=item_catalog(sale_prices=False, purchase_prices=True), scan_words=scan_words(lang), tax_rates=rates_by_item(), units=units_catalog(purchase=True)),
    )


@require_permission("purchases.view_purchase_invoices")
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        entity_scope.scope(PurchaseInvoice.objects.select_related("supplier", "receiving_location", "cashbox").prefetch_related("lines__item", "returns"), entity_scope.PURCHASE_INVOICE),
        pk=pk,
    )
    return render(
        request,
        "purchases/detail.html",
        _context(
            request,
            invoice=invoice,
            can_post=user_has_permission(request.user, "purchases.create_purchase_invoice"),
            can_return=user_has_permission(request.user, "purchases.return_purchase"),
        ),
    )


@require_permission("purchases.create_purchase_invoice")
def invoice_post(request, pk):
    if request.method != "POST":
        return redirect("purchases:detail", pk=pk)
    lang = _lang(request)
    entity_scope.get_or_404(PurchaseInvoice, entity_scope.PURCHASE_INVOICE, pk=pk)
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
    entity_scope.get_or_404(PurchaseInvoice, entity_scope.PURCHASE_INVOICE, pk=pk)
    try:
        cancel_posted_purchase_invoice(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["cancelled"])
    return redirect(f"/purchases/{pk}/?lang={lang}")


@require_permission("purchases.return_purchase")
def return_create(request, pk):
    invoice = get_object_or_404(
        entity_scope.scope(PurchaseInvoice.objects.select_related("supplier", "receiving_location", "cashbox").prefetch_related("lines__item"), entity_scope.PURCHASE_INVOICE),
        pk=pk,
    )
    lang = _lang(request)
    form = PurchaseReturnForm(request.POST or None, lang=lang)
    line_formset = PurchaseReturnLineFormSet(
        request.POST or None,
        prefix="lines",
        form_kwargs={"invoice": invoice, "lang": lang},
    )
    if request.method == "POST" and form.is_valid() and line_formset.is_valid():
        line_data = [
            row.cleaned_data for row in line_formset.forms if row.cleaned_data.get("source_line")
        ]
        try:
            purchase_return = create_purchase_return(
                source_invoice_id=invoice.pk,
                lines=line_data,
                user=request.user,
                **form.cleaned_data,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, STRINGS[lang]["return_saved"])
            return redirect(f"/purchases/returns/{purchase_return.pk}/?lang={lang}")
    return render(
        request,
        "purchases/return_form.html",
        _context(request, invoice=invoice, form=form, line_formset=line_formset),
    )


@require_permission("purchases.view_purchase_invoices")
def return_detail(request, pk):
    purchase_return = get_object_or_404(
        PurchaseReturn.objects.select_related(
            "source_invoice__supplier", "source_invoice__receiving_location", "source_invoice__cashbox"
        ).prefetch_related("lines__source_line__item").filter(entity_scope.q(entity_scope.PURCHASE_RETURN)),
        pk=pk,
    )
    return render(
        request,
        "purchases/return_detail.html",
        _context(
            request,
            purchase_return=purchase_return,
            can_reverse=user_has_permission(request.user, "purchases.return_purchase"),
            reversal_form=PurchaseReturnReversalForm(lang=_lang(request)),
        ),
    )


@require_permission("purchases.return_purchase")
def return_cancel(request, pk):
    if request.method != "POST":
        return redirect("purchases:return_detail", pk=pk)
    lang = _lang(request)
    entity_scope.get_or_404(PurchaseReturn, entity_scope.PURCHASE_RETURN, pk=pk)
    form = PurchaseReturnReversalForm(request.POST, lang=lang)
    if form.is_valid():
        try:
            cancel_purchase_return(pk, user=request.user, **form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, STRINGS[lang]["return_reversed"])
    else:
        messages.error(request, "; ".join(error for errors in form.errors.values() for error in errors))
    return redirect(f"/purchases/returns/{pk}/?lang={lang}")


@require_permission("purchases.pay_supplier")
def payment_list(request):
    queryset = entity_scope.scope(SupplierPayment.objects.select_related("supplier", "cashbox", "created_by"), entity_scope.SUPPLIER_PAYMENT)
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
        form = SupplierPaymentForm(lang=lang, initial=_party_initial(request, "supplier"))
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
    entity_scope.get_or_404(SupplierPayment, entity_scope.SUPPLIER_PAYMENT, pk=pk)
    try:
        cancel_supplier_payment(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["payment_cancelled"])
    return redirect(f"/purchases/payments/?lang={lang}")
