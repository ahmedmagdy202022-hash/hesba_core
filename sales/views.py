import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from barcode.services import item_catalog, scan_words
from master_data.quick import offer_quick_add
from pricing.services import price_book
from loyalty import services as loyalty
from promotions import services as promotions
from taxes.services import create_sales_draft_with_tax, rates_by_item, vat_enabled
from serials.services import attach_sale_serials, check_invoice_serials, prepare_sale_serials
from units.services import convert_lines, units_catalog
from permissions.decorators import require_permission
from entities import scope as entity_scope
from permissions.services import user_has_permission
from config.return_table import chosen_lines, return_table

from .forms import (
    CustomerPaymentForm,
    SalesDraftForm,
    SalesLineFormSet,
    SalesReturnForm,
    SalesReturnTableFormSet,
    SalesReturnReversalForm,
)
from .models import CustomerPayment, SalesInvoice, SalesReturn
from .return_reasons import return_reasons
from .services import (
    cancel_customer_payment,
    cancel_posted_sales_invoice,
    cancel_sales_return,
    create_sales_return,
    post_sales_invoice,
    record_customer_payment,
)
from config.paging import page_of


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
        "collections": "تحصيلات العملاء",
        "new_collection": "تحصيل جديد من عميل",
        "collection_saved": "تم تسجيل تحصيل العميل.",
        "collection_cancelled": "تم إلغاء تحصيل العميل وعكس آثاره.",
        "new_return": "مرتجع بيع جديد",
        "return_saved": "تم ترحيل مرتجع البيع.",
        "return_reversed": "تم عكس مرتجع البيع.",
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
        "collections": "Customer collections",
        "new_collection": "New customer collection",
        "collection_saved": "Customer collection recorded.",
        "collection_cancelled": "Customer collection cancelled and reversed.",
        "new_return": "New sales return",
        "return_saved": "Sales return posted.",
        "return_reversed": "Sales return reversed.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _party_initial(request, field):
    """PARTY-001: "New invoice" / "Collect" from a party card pre-selects that party."""

    value = request.GET.get(field) or ""
    return {field: value} if value.isdigit() else None


def _activity_invoices(lang):
    """AUDIT-3: the sales list is titled in the activity's own words ("Orders &
    bills" in a restaurant); a shop keeps "Sales invoices"."""

    from settings_core.vocabulary import BASE, term

    words = term("operations", lang)
    return {"invoices": words} if words != BASE["operations"][lang] else {}


def _context(request, **extra):
    from settings_core.vocabulary import term

    lang = _lang(request)
    context = {
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        # AUDIT-3: "new order" in a restaurant, "new visit bill" in a clinic...
        "words": dict(STRINGS[lang], new=term("new_sale", lang), **_activity_invoices(lang)),
        "section": "sales",
        "page_title": STRINGS[lang]["page_title"],
    }
    context.update(extra)
    return context


@require_permission("sales.view_sales_invoices")
def invoice_list(request):
    queryset = entity_scope.scope(SalesInvoice.objects.select_related("customer", "selling_location", "cashbox"), entity_scope.SALES_INVOICE)
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
    page = page_of(request, queryset)
    return render(request, "sales/list.html", _context(request, page=page, query=query, status_filter=status,
                                                      can_create=user_has_permission(request.user, "sales.create_sales_invoice")))


@require_permission("sales.create_sales_invoice")
def invoice_create(request):
    from orders.services import invoice_initial, link_invoice, order_for_invoice
    from pharmacy import services as pharmacy

    lang = _lang(request)
    source = request.POST if request.method == "POST" else request.GET
    from_order = order_for_invoice(request.user, source.get("order"))  # T1
    from_prescription = None if from_order is not None else pharmacy.prescription_for_invoice(request.user, source.get("prescription"), saving=request.method == "POST")  # T10
    if request.method == "POST":
        form = SalesDraftForm(request.POST, lang=lang)
        if vat_enabled():
            del form.fields["tax_amount"]  # TAX-001: charged per line from item rates
        line_formset = SalesLineFormSet(request.POST, prefix="lines", form_kwargs={"lang": lang})
        if form.is_valid() and line_formset.is_valid():
            line_data = [row.cleaned_data for row in line_formset.forms if row.cleaned_data.get("item") is not None]
            try:
                line_data = convert_lines(line_data, "unit_sale_price", lang)  # UNITS-001
                line_data = prepare_sale_serials(line_data, lang)  # SERIAL-001
                line_data, promotion, promotion_off = promotions.apply(line_data, coupon=request.POST.get("coupon"), lang=lang)  # T5
                # T6: points spent go on top of the invoice's own discount; the spending is booked when it is posted.
                points, points_off = loyalty.redeem(form.cleaned_data.get("customer"), request.POST.get("redeem_points"), loyalty.lines_total(line_data), lang)
                if points:
                    form.cleaned_data["discount_amount"] = (form.cleaned_data.get("discount_amount") or 0) + points_off
                def create():
                    invoice = create_sales_draft_with_tax(form.cleaned_data, line_data, request.user)
                    attach_sale_serials(invoice, line_data)
                    promotions.record(invoice, promotion, promotion_off, request.POST.get("coupon"))  # T5: for the promotions report
                    loyalty.remember(invoice, points, points_off)  # T6
                    if from_order is not None:  # T1: this invoice takes its quantities off the order
                        link_invoice(from_order, invoice, request.user, lang)
                    if from_prescription is not None:  # T10: this invoice dispenses the prescription
                        pharmacy.link_dispense(from_prescription, invoice, request.user, lang)
                    return invoice

                invoice = form.create_with_fresh_number(create)  # AUTONUM: atomic, retried on a taken number
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                # R2-6: "save and post" in one press; UI-TRACK F23: one message, "posted", unless posting failed.
                if request.POST.get("then") == "post":
                    _post_now(request, invoice.pk, lang, saved_first=True)
                else:
                    messages.success(request, STRINGS[lang]["saved"])
                return redirect(f"/sales/{invoice.pk}/?lang={lang}")
    else:
        initial = _party_initial(request, "customer") or {}
        line_initial = []
        if from_order is not None:  # T1: what is left on the order, at the order's prices
            order_header, line_initial = invoice_initial(from_order, lang)
            initial.update({key: getattr(value, "pk", value) for key, value in order_header.items()})
        elif from_prescription is not None:  # T10: the prescription's drugs, at the customer's price
            rx_header, line_initial = pharmacy.invoice_initial(from_prescription, lang)
            initial.update({key: getattr(value, "pk", value) for key, value in rx_header.items()})
        if not initial.get("customer"):
            from settings_core.setup_services import enabled_modules

            if "customers" not in enabled_modules():  # AUDIT-3: a restaurant sells to walk-ins
                from .pos import walk_in_customer

                initial["customer"] = walk_in_customer().pk
        form = SalesDraftForm(lang=lang, initial=initial)
        if vat_enabled():
            del form.fields["tax_amount"]
        line_formset = SalesLineFormSet(prefix="lines", form_kwargs={"lang": lang}, initial=line_initial or None)
    offer_quick_add(form, request.user, lang)  # FB2-QUICKPARTY: + new supplier / customer / warehouse
    return render(request, "sales/form.html", _context(request, form=form, line_formset=line_formset, can_post=True, item_catalog=item_catalog(sale_prices=True, purchase_prices=False), scan_words=scan_words(lang), price_book=price_book(), tax_rates=rates_by_item(), units=units_catalog(), from_order=from_order, from_prescription=from_prescription,
                                                      promotions_on=promotions.enabled(), running_promotions=promotions.running() if promotions.enabled() else [],
                                                      coupon=request.POST.get("coupon", ""), loyalty_on=loyalty.enabled(), loyalty_rules=loyalty.rules() if loyalty.enabled() else None,
                                                      redeem_points=request.POST.get("redeem_points", "")))


@require_permission("sales.view_sales_invoices")
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        entity_scope.scope(SalesInvoice.objects.select_related("customer", "selling_location", "cashbox").prefetch_related("lines__item", "returns"), entity_scope.SALES_INVOICE),
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
            can_post=user_has_permission(request.user, "sales.create_sales_invoice"),
            can_return=user_has_permission(request.user, "sales.return_sale"),
            einvoice_problems=_einvoice_problems(invoice, _lang(request)),
            short_box=_short_box(invoice, request.user),
        ),
    )


def _short_box(invoice, user):
    """HG-065: the items a draft would take below zero, for the box that asks the seller to agree (None: no box)."""

    from . import short_sales

    if invoice.status != "draft" or not user_has_permission(user, "sales.create_sales_invoice") or not short_sales.enabled():
        return None
    return short_sales.shortages(invoice, list(invoice.lines.all())) or None


def _einvoice_problems(invoice, lang):
    """R2-6: how many things the e-invoice document still lacks (None when e-invoicing is off or the invoice is a draft)."""

    from einvoice.services import build_document
    from settings_core.capabilities import capability_enabled

    if invoice.status != "posted" or not capability_enabled("e_invoice"):
        return None
    try:
        return len(build_document(invoice, lang)[1])
    except Exception:  # a badge only: a malformed e-invoice setting must not break the invoice page
        logging.getLogger("hesba.einvoice").exception("E-invoice readiness failed for %s", invoice.invoice_number)
        return None


def _post_now(request, pk, lang, saved_first=False):
    from shifts.gate import require_open_shift

    invoice = entity_scope.get_or_404(SalesInvoice, entity_scope.SALES_INVOICE, pk=pk)  # another entity's invoice stays a 404
    try:
        require_open_shift(request.user, lang)  # AN4: no sale is posted without an open shift
        with transaction.atomic():
            check_invoice_serials(invoice, lang)  # SERIAL-001: still in stock?
            # HG-065: the seller ticked the box that agrees to sell what is not in stock
            post_sales_invoice(pk, request.user, allow_short=request.POST.get("allow_short") == "1", short_source="invoice")
    except ValidationError as exc:
        # Codex #233: one message either way; after "save and post" it also says the draft was kept.
        reason = "; ".join(exc.messages)
        messages.error(request, f"{STRINGS[lang]['saved']}; {reason}" if saved_first else reason)  # "; " so each part is still translated (ui_messages)
        return False
    messages.success(request, STRINGS[lang]["posted"])
    return True


@require_permission("sales.create_sales_invoice")
def invoice_post(request, pk):
    if request.method != "POST":
        return redirect("sales:detail", pk=pk)
    lang = _lang(request)
    _post_now(request, pk, lang)
    return redirect(f"/sales/{pk}/?lang={lang}")


@require_permission("sales.return_sale")
def invoice_cancel(request, pk):
    if request.method != "POST":
        return redirect("sales:detail", pk=pk)
    lang = _lang(request)
    entity_scope.get_or_404(SalesInvoice, entity_scope.SALES_INVOICE, pk=pk)
    try:
        cancel_posted_sales_invoice(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["cancelled"])
    return redirect(f"/sales/{pk}/?lang={lang}")


@require_permission("sales.return_sale")
def return_create(request, pk):
    invoice = get_object_or_404(
        entity_scope.scope(SalesInvoice.objects.select_related("customer", "selling_location", "cashbox").prefetch_related("lines__item"), entity_scope.SALES_INVOICE),
        pk=pk,
    )
    lang = _lang(request)
    form = SalesReturnForm(request.POST or None, lang=lang)
    # UI-TRACK F15: one row per line of the invoice, not five empty pickers.
    line_formset = return_table(SalesReturnTableFormSet, invoice, request.POST or None, lang, "sales_return")
    if request.method == "POST" and form.is_valid() and line_formset.is_valid():
        line_data = chosen_lines(line_formset)
        try:
            sales_return = form.create_with_fresh_number(lambda: create_sales_return(  # AUTONUM
                source_invoice_id=invoice.pk,
                lines=line_data,
                user=request.user,
                **form.cleaned_data,
            ))
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, STRINGS[lang]["return_saved"])
            return redirect(f"/sales/returns/{sales_return.pk}/?lang={lang}")
    return render(
        request,
        "sales/return_form.html",
        _context(request, invoice=invoice, form=form, line_formset=line_formset, return_reasons=return_reasons(_lang(request))),
    )


@require_permission("sales.view_sales_invoices")
def return_detail(request, pk):
    sales_return = get_object_or_404(
        SalesReturn.objects.select_related(
            "source_invoice__customer", "source_invoice__selling_location", "source_invoice__cashbox"
        ).prefetch_related("lines__source_line__item").filter(entity_scope.q(entity_scope.SALES_RETURN)),
        pk=pk,
    )
    return render(
        request,
        "sales/return_detail.html",
        _context(
            request,
            sales_return=sales_return,
            can_reverse=user_has_permission(request.user, "sales.return_sale"),
            reversal_form=SalesReturnReversalForm(lang=_lang(request)),
        ),
    )


@require_permission("sales.return_sale")
def return_cancel(request, pk):
    if request.method != "POST":
        return redirect("sales:return_detail", pk=pk)
    lang = _lang(request)
    entity_scope.get_or_404(SalesReturn, entity_scope.SALES_RETURN, pk=pk)
    form = SalesReturnReversalForm(request.POST, lang=lang)
    if form.is_valid():
        try:
            cancel_sales_return(pk, user=request.user, **form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, STRINGS[lang]["return_reversed"])
    else:
        messages.error(request, "; ".join(error for errors in form.errors.values() for error in errors))
    return redirect(f"/sales/returns/{pk}/?lang={lang}")


@require_permission("sales.receive_customer_payment")
def payment_list(request):
    queryset = entity_scope.scope(CustomerPayment.objects.select_related("customer", "cashbox", "created_by"), entity_scope.CUSTOMER_PAYMENT)
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
    page = page_of(request, queryset)
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
                form.create_with_fresh_number(lambda: record_customer_payment(user=request.user, **form.cleaned_data))
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, STRINGS[lang]["collection_saved"])
                return redirect(f"/sales/collections/?lang={lang}")
    else:
        form = CustomerPaymentForm(lang=lang, initial=_party_initial(request, "customer"))
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
    entity_scope.get_or_404(CustomerPayment, entity_scope.CUSTOMER_PAYMENT, pk=pk)
    try:
        cancel_customer_payment(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["collection_cancelled"])
    return redirect(f"/sales/collections/?lang={lang}")
