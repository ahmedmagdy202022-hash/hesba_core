"""T1 screens: the quotations and orders list, a new quotation, one quotation or order, its print and the delivery note."""

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer, Item
from permissions.decorators import require_permission

from . import services
from .models import OFFERED, SalesOrderLine, Status

WORDS = {
    "ar": {
        "title": "عروض الأسعار والأوامر", "intro": "عرض السعر بيتبعت للعميل، ولما يوافق بيبقى أمر بيع تفوتره مرة واحدة أو على دفعات.",
        "new": "+ عرض سعر جديد", "search": "دوّر برقم العرض أو الأمر أو العميل", "find": "بحث",
        "f_offered": "عروض مفتوحة", "f_orders": "أوامر مفتوحة", "f_done": "اتفوترت أو اتقفلت", "f_all": "الكل",
        "number": "الرقم", "customer": "العميل", "date": "التاريخ", "valid_until": "صالح لحد", "status": "الحالة", "total": "الإجمالي",
        "open": "افتح", "none": "مفيش عروض هنا.", "back": "عروض الأسعار والأوامر", "choose": "اختار", "save": "حفظ",
        "quote_date": "تاريخ العرض", "salesperson": "المندوب", "notes": "ملاحظات", "terms": "الشروط (التسليم والدفع)",
        "terms_hint": "بتتطبع تحت العرض. مثلًا: التسليم خلال 7 أيام، الدفع 50% مقدم والباقي عند التسليم.",
        "create_btn": "اعمل العرض وضيف الأصناف", "details": "بيانات العرض", "lines": "الأصناف", "item": "الصنف / الخدمة", "qty": "الكمية",
        "price": "السعر", "price_hint": "سيبه فاضي ياخد سعر العميل", "discount": "خصم %", "amount": "القيمة", "add": "ضيف", "remove": "شيل",
        "no_lines": "لسه مفيش أصناف على العرض.", "gross": "الإجمالي قبل الخصم", "discount_total": "الخصم", "tax": "الضريبة", "net_total": "الصافي",
        "ordered": "المطلوب", "invoiced": "اتفوتر", "drafted": "في مسودة", "left": "الباقي", "next": "الخطوة الجاية",
        "make_invoice": "اعمل فاتورة بالباقي", "make_invoice_hint": "بتفتح فاتورة البيع العادية فيها الباقي من الأمر. تقدر تغيّر الكميات أو تفوتر جزء بس.",
        "invoices": "الفواتير اللي اتعملت من الأمر", "no_invoices": "لسه مفيش فواتير من الأمر ده.", "delivery_note": "إذن تسليم",
        "print_quote": "اطبع عرض السعر", "print_order": "اطبع أمر البيع", "whatsapp": "ابعته واتساب", "copy": "نسخ الرابط",
        "saved": "اتحفظ.", "moved": "«{number}» بقى «{status}».", "created": "اتعمل عرض السعر {number}.", "order_number": "رقم الأمر",
        "quote_number": "رقم العرض", "view_only": "تقدر تشوف العرض بس.", "share_text": "عرض سعر {number} من {company}: {url}",
        "expired_hint": "صلاحية العرض انتهت يوم {date}. تقدر تعدّل التاريخ وتبعته تاني.", "doc_quote": "عرض سعر", "doc_order": "أمر بيع", "storekeeper": "توقيع أمين المخزن",
    },
    "en": {
        "title": "Quotations & orders", "intro": "A quotation goes to the customer; once they accept, it becomes a sales order you invoice in one go or in parts.",
        "new": "+ New quotation", "search": "Search by quotation or order number, or customer", "find": "Search",
        "f_offered": "Open quotations", "f_orders": "Open orders", "f_done": "Invoiced or closed", "f_all": "All",
        "number": "No.", "customer": "Customer", "date": "Date", "valid_until": "Valid until", "status": "Status", "total": "Total",
        "open": "Open", "none": "No quotations here.", "back": "Quotations & orders", "choose": "Choose", "save": "Save",
        "quote_date": "Quotation date", "salesperson": "Salesperson", "notes": "Notes", "terms": "Terms (delivery and payment)",
        "terms_hint": "Printed under the quotation. For example: delivery within 7 days, 50% in advance and the rest on delivery.",
        "create_btn": "Create the quotation and add items", "details": "Quotation details", "lines": "Items", "item": "Item / service", "qty": "Qty",
        "price": "Price", "price_hint": "Leave empty for the customer's price", "discount": "Discount %", "amount": "Amount", "add": "Add", "remove": "Remove",
        "no_lines": "No items on the quotation yet.", "gross": "Subtotal", "discount_total": "Discount", "tax": "VAT", "net_total": "Net total",
        "ordered": "Ordered", "invoiced": "Invoiced", "drafted": "On a draft", "left": "Left", "next": "Next step",
        "make_invoice": "Invoice what is left", "make_invoice_hint": "Opens the ordinary sales invoice with what is left on the order. You can change the quantities or invoice part of it.",
        "invoices": "Invoices made from the order", "no_invoices": "No invoices from this order yet.", "delivery_note": "Delivery note",
        "print_quote": "Print the quotation", "print_order": "Print the sales order", "whatsapp": "Send on WhatsApp", "copy": "Copy link",
        "saved": "Saved.", "moved": "“{number}” is now “{status}”.", "created": "Quotation {number} created.", "order_number": "Order no.",
        "quote_number": "Quotation no.", "view_only": "You can view this quotation only.", "share_text": "Quotation {number} from {company}: {url}",
        "expired_hint": "This quotation expired on {date}. Change the date and send it again.", "doc_quote": "Quotation", "doc_order": "Sales order", "storekeeper": "Storekeeper's signature",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _guard(request):
    if not services.enabled():
        raise Http404("Quotations are switched off.")
    if not services.can_view(request.user):
        raise PermissionDenied("Quotations need sales.view_sales_invoices.")


def _base(request, lang, **extra):
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"], "section": "quotes",
            "labels": services.STATUS_LABELS[lang], "can_manage": services.can_manage(request.user), **extra}


def _error(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(getattr(exc, "messages", [str(exc)])), lang)


def _salespeople():
    from staff.services import reps_offered

    return reps_offered()


@require_permission(services.VIEW)
def board(request):
    _guard(request)
    lang = _lang(request)
    today = timezone.localdate()
    show = request.GET.get("show") or "offered"
    rows = services.scoped().select_related("customer")
    if show == "orders":
        rows = rows.filter(status=Status.CONFIRMED)
    elif show == "done":
        rows = rows.filter(status__in=(Status.CLOSED, Status.CONFIRMED, Status.REJECTED, Status.CANCELLED))
    elif show != "all":
        show = "offered"
        rows = rows.filter(status__in=OFFERED)
    query = (request.GET.get("q") or "").strip()
    if query:
        from django.db.models import Q

        rows = rows.filter(Q(number__icontains=query) | Q(order_number__icontains=query) | Q(customer__name__icontains=query))
    rows = list(rows.prefetch_related("lines__item")[:200])
    shown = []
    for row in rows:
        row.state = services.state(row, today)
        if show == "orders" and row.state == "done":
            continue
        if show == "done" and row.status == Status.CONFIRMED and row.state != "done":
            continue
        row.total = services.totals(row)["total"]
        shown.append(row)
    return render(request, "orders/board.html", _base(request, lang, rows=shown, show=show, query=query, counts=services.counts()))


def new(request):
    _guard(request)
    if not services.can_manage(request.user):
        raise PermissionDenied("A quotation needs sales.create_sales_invoice.")
    lang = _lang(request)
    error = ""
    if request.method == "POST":
        data = {key: request.POST.get(key) for key in request.POST}
        data["customer"] = Customer.objects.filter(pk=request.POST.get("customer") or 0, active=True).first()
        data["salesperson"] = _salespeople().filter(pk=request.POST.get("salesperson") or 0).first()
        try:
            order = services.create_quote(data, request.user, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, WORDS[lang]["created"].format(number=order.number))
            return redirect(f"{reverse('orders:detail', args=[order.pk])}?lang={lang}")
    chosen = request.POST.get("customer") or request.GET.get("customer") or ""
    customers = Customer.objects.filter(active=True).exclude(customer_code="WALK-IN").order_by("name")[:500]
    today = timezone.localdate()
    return render(request, "orders/new.html", _base(request, lang, customers=customers, chosen=chosen, post=request.POST, error=error,
                                                    salespeople=_salespeople(), today=today,
                                                    valid_default=today + timedelta(days=services.DEFAULT_VALID_DAYS)))


def detail(request, pk):
    _guard(request)
    lang = _lang(request)
    order = get_object_or_404(services.scoped().select_related("customer", "salesperson"), pk=pk)
    words = WORDS[lang]
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "move":
                to = request.POST.get("to") or ""
                order = services.move(order, to, request.user, lang)
                message = words["moved"].format(number=order.display_number, status=services.STATUS_LABELS[lang].get(to, to))
            elif action == "details":
                data = {key: request.POST.get(key) for key in request.POST}
                data["salesperson"] = _salespeople().filter(pk=request.POST.get("salesperson") or 0).first()
                services.update_header(order, data, request.user, lang)
                message = words["saved"]
            elif action == "add_line":
                item = Item.objects.filter(pk=request.POST.get("item") or 0, active=True).first()
                services.add_line(order, item, request.user, quantity=request.POST.get("quantity"), unit_price=request.POST.get("unit_price"),
                                  discount_percent=request.POST.get("discount_percent"), description=request.POST.get("description", ""), lang=lang)
                message = words["saved"]
            elif action == "remove_line":
                line = get_object_or_404(SalesOrderLine, pk=request.POST.get("line") or 0, order=order)
                services.remove_line(line, request.user, lang)
                message = words["saved"]
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, message)
            return redirect(f"{reverse('orders:detail', args=[order.pk])}?lang={lang}")
        order.refresh_from_db()
    today = timezone.localdate()
    rows = services.quantities(order)
    numbers = services.totals(order)
    can_manage = services.can_manage(request.user)
    state = services.state(order, today, rows)
    hidden = set()
    if order.status == Status.CONFIRMED and any(row["invoiced"] or row["drafted"] for row in rows):
        hidden.add(Status.CANCELLED)  # it has invoices: it closes, it is not cancelled
    if state == "done":
        hidden.add(Status.CLOSED)  # nothing is left to close
    moves = [(status, services.MOVE_LABELS[lang][status]) for status in services.NEXT.get(order.status, ()) if status not in hidden] if can_manage else []
    items = Item.objects.filter(active=True).order_by("item_name")[:500] if can_manage and order.status in OFFERED else []
    share = None
    if order.status in OFFERED or order.is_order:
        from printing.company import company_details
        from printing.share import share_url, whatsapp_link

        url = share_url(request, "sales_quote", order.pk, lang)
        share = {"url": url, "whatsapp": whatsapp_link(order.customer.phone, words["share_text"].format(
            number=order.display_number, company=company_details()["name"], url=url))}
    return render(request, "orders/detail.html", _base(
        request, lang, order=order, rows=rows, numbers=numbers, moves=moves, items=items, error=error, today=today,
        state=state, editable=can_manage and order.status in OFFERED, share=share,
        can_invoice=can_manage and order.status == Status.CONFIRMED and any(row["left"] > 0 for row in rows),
        invoices=services.invoices(order), salespeople=_salespeople() if can_manage else [],
        expired_hint=words["expired_hint"].format(date=order.valid_until.strftime("%Y-%m-%d")) if order.valid_until else ""))


def _doc(order, lang, *, delivery_of=None):
    """The printing document of the quotation (or order), or of one invoice's delivery note."""

    from printing.views import WORDS as PRINT_WORDS

    words, own = PRINT_WORDS[lang], WORDS[lang]
    if delivery_of is not None:
        lines = [{"label": line.description or line.item.search_label, "quantity": line.quantity}
                 for line in delivery_of.lines.select_related("item").order_by("line_number")]
        return {"kind": "delivery_note", "title": own["delivery_note"], "number": delivery_of.invoice_number, "date": delivery_of.invoice_date,
                "status": "posted" if delivery_of.status == "posted" else delivery_of.status, "status_label": "", "no_prices": True, "signature_second": own["storekeeper"],
                "party_label": words["customer"], "party": {"name": order.customer.name, "phone": order.customer.phone, "address": order.customer.address},
                "reference": (own["order_number"], order.display_number), "lines": lines, "has_discounts": False, "totals": [],
                "notes": order.notes}
    numbers = services.totals(order)
    lines = [{"label": line.description or line.item.search_label, "quantity": line.quantity, "unit_price": line.unit_price,
              "discount": line.discount, "total": line.amount} for line in numbers["lines"]]
    totals = [(words["subtotal"], numbers["gross"])]
    if numbers["discount"]:
        totals.append((words["discount"], -numbers["discount"]))
    if numbers["tax"]:
        totals.append((words["tax"], numbers["tax"]))
    is_order = order.is_order
    extra = [(own["valid_until"], order.valid_until.strftime("%Y-%m-%d"))] if order.valid_until and not is_order else []
    return {"kind": "sales_order" if is_order else "sales_quote", "title": own["doc_order"] if is_order else own["doc_quote"],
            "number": order.display_number, "date": order.quote_date, "status": "posted" if order.status in (Status.SENT, Status.CONFIRMED, Status.CLOSED) else order.status,
            "status_label": services.STATUS_LABELS[lang].get(order.status, ""), "party_label": words["customer"],
            "party": {"name": order.customer.name, "phone": order.customer.phone, "address": order.customer.address},
            "reference": (own["quote_number"], order.number) if is_order else (extra[0] if extra else None), "lines": lines,
            "has_discounts": any(line["discount"] for line in lines), "totals": totals, "grand_total": numbers["total"],
            "salesperson": order.salesperson, "notes": order.notes, "terms": order.terms, "words_amount": numbers["total"]}


def print_order(request, pk):
    from printing.views import _render

    _guard(request)
    order = get_object_or_404(services.scoped().select_related("customer", "salesperson"), pk=pk)
    return _render(request, _doc(order, _lang(request)), f"/orders/{pk}/")


def delivery_note(request, pk, invoice_pk):
    from printing.views import _render

    _guard(request)
    order = get_object_or_404(services.scoped().select_related("customer"), pk=pk)
    invoice = get_object_or_404(services.invoices(order), pk=invoice_pk)
    return _render(request, _doc(order, _lang(request), delivery_of=invoice), f"/orders/{pk}/")
