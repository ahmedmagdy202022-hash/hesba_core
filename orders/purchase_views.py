"""T2 screens: purchase orders, one order, its print, sent to the supplier and received on ordinary purchase invoices."""

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Item, Supplier
from permissions.decorators import require_permission

from . import purchasing as po
from .models import PurchaseOrderLine, PurchaseStatus as Status
from .views import _error, _lang

WORDS = {
    "ar": {
        "title": "أوامر الشراء", "intro": "الطلب بيتبعت للمورد، ولما يأكّد بتستلم البضاعة بفاتورة شراء عادية، مرة واحدة أو على دفعات.",
        "new": "+ أمر شراء جديد", "search": "دوّر برقم الأمر أو المورد", "find": "بحث", "f_open": "لسه مابعتش", "f_waiting": "مستني الاستلام",
        "f_late": "متأخر", "f_done": "اتستلم أو اتقفل", "f_all": "الكل", "number": "الرقم", "supplier": "المورد", "date": "التاريخ",
        "expected": "ميعاد الاستلام", "status": "الحالة", "total": "الإجمالي", "none": "مفيش أوامر شراء هنا.", "back": "أوامر الشراء",
        "choose": "اختار", "save": "حفظ", "order_date": "تاريخ الأمر", "notes": "ملاحظات", "terms": "الشروط (التسليم والدفع)",
        "terms_hint": "بتتطبع تحت الأمر. مثلًا: التوريد في المخزن الرئيسي، الدفع بعد 30 يوم.", "create_btn": "اعمل الأمر وضيف الأصناف",
        "details": "بيانات الأمر", "lines": "الأصناف", "item": "الصنف", "qty": "الكمية", "price": "سعر الشراء", "price_hint": "سيبه فاضي ياخد آخر تكلفة",
        "discount": "خصم %", "amount": "القيمة", "add": "ضيف", "remove": "شيل", "no_lines": "لسه مفيش أصناف على الأمر.",
        "gross": "الإجمالي قبل الخصم", "discount_total": "الخصم", "tax": "الضريبة", "net_total": "الصافي", "received": "اتستلم", "drafted": "في مسودة",
        "left": "الباقي", "next": "الخطوة الجاية", "receive": "استلم الباقي بفاتورة شراء",
        "receive_hint": "بتفتح فاتورة الشراء العادية فيها الباقي من الأمر. غيّر الكميات لو اللي وصل جزء بس.",
        "receipts": "فواتير الشراء اللي استلمت الأمر", "no_receipts": "لسه مفيش استلام على الأمر ده.", "print": "اطبع أمر الشراء",
        "whatsapp": "ابعته للمورد واتساب", "saved": "اتحفظ.", "moved": "«{number}» بقى «{status}».", "created": "اتعمل أمر الشراء {number}.",
        "share_text": "أمر شراء {number} من {company}:\n{lines}", "doc": "أمر شراء",
    },
    "en": {
        "title": "Purchase orders", "intro": "The order goes to the supplier; once confirmed, the goods are received on an ordinary purchase invoice, in one go or in parts.",
        "new": "+ New purchase order", "search": "Search by order number or supplier", "find": "Search", "f_open": "Not sent yet", "f_waiting": "Waiting for delivery",
        "f_late": "Late", "f_done": "Received or closed", "f_all": "All", "number": "No.", "supplier": "Supplier", "date": "Date",
        "expected": "Expected on", "status": "Status", "total": "Total", "none": "No purchase orders here.", "back": "Purchase orders",
        "choose": "Choose", "save": "Save", "order_date": "Order date", "notes": "Notes", "terms": "Terms (delivery and payment)",
        "terms_hint": "Printed under the order. For example: deliver to the main store, payment after 30 days.", "create_btn": "Create the order and add items",
        "details": "Order details", "lines": "Items", "item": "Item", "qty": "Qty", "price": "Purchase price", "price_hint": "Leave empty for the last cost",
        "discount": "Discount %", "amount": "Amount", "add": "Add", "remove": "Remove", "no_lines": "No items on the order yet.",
        "gross": "Subtotal", "discount_total": "Discount", "tax": "VAT", "net_total": "Net total", "received": "Received", "drafted": "On a draft",
        "left": "Left", "next": "Next step", "receive": "Receive what is left on a purchase invoice",
        "receive_hint": "Opens the ordinary purchase invoice with what is left on the order. Change the quantities if only part arrived.",
        "receipts": "Purchase invoices that received the order", "no_receipts": "Nothing received on this order yet.", "print": "Print the purchase order",
        "whatsapp": "Send to the supplier on WhatsApp", "saved": "Saved.", "moved": "“{number}” is now “{status}”.", "created": "Purchase order {number} created.",
        "share_text": "Purchase order {number} from {company}:\n{lines}", "doc": "Purchase order",
    },
}


def _guard(request):
    if not po.enabled():
        raise Http404("Purchases are switched off.")
    if not po.can_view(request.user):
        raise PermissionDenied("Purchase orders need purchases.view_purchase_invoices.")


def _base(request, lang, **extra):
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"], "section": "purchases",
            "labels": po.STATUS_LABELS[lang], "can_manage": po.can_manage(request.user), **extra}


@require_permission(po.VIEW)
def board(request):
    _guard(request)
    lang = _lang(request)
    today = timezone.localdate()
    show = request.GET.get("show") or "waiting"
    rows = po.scoped().select_related("supplier")
    if show == "open":
        rows = rows.filter(status__in=(Status.DRAFT, Status.SENT))
    elif show == "late":
        rows = rows.filter(status=Status.CONFIRMED, expected_on__lt=today)
    elif show == "done":
        rows = rows.filter(status__in=(Status.CONFIRMED, Status.CLOSED, Status.CANCELLED))
    elif show != "all":
        show = "waiting"
        rows = rows.filter(status=Status.CONFIRMED)
    query = (request.GET.get("q") or "").strip()
    if query:
        from django.db.models import Q

        rows = rows.filter(Q(number__icontains=query) | Q(supplier__name__icontains=query))
    shown = []
    for row in rows.prefetch_related("lines__item")[:200]:
        row.state = po.state(row, today)
        if show in ("waiting", "late") and row.state == "done":
            continue
        if show == "done" and row.status == Status.CONFIRMED and row.state != "done":
            continue
        row.total = po.totals(row)["total"]
        shown.append(row)
    return render(request, "orders/po_board.html", _base(request, lang, rows=shown, show=show, query=query, counts=po.counts()))


def new(request):
    _guard(request)
    if not po.can_manage(request.user):
        raise PermissionDenied("A purchase order needs purchases.create_purchase_invoice.")
    lang = _lang(request)
    error = ""
    if request.method == "POST":
        data = {key: request.POST.get(key) for key in request.POST}
        data["supplier"] = Supplier.objects.filter(pk=request.POST.get("supplier") or 0, active=True).first()
        try:
            order = po.create_order(data, request.user, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, WORDS[lang]["created"].format(number=order.number))
            return redirect(f"{reverse('purchase_orders:detail', args=[order.pk])}?lang={lang}")
    today = timezone.localdate()
    return render(request, "orders/po_new.html", _base(request, lang, suppliers=Supplier.objects.filter(active=True).order_by("name")[:500],
                                                       chosen=request.POST.get("supplier") or request.GET.get("supplier") or "", post=request.POST,
                                                       error=error, today=today, expected_default=today + timedelta(days=7)))


def detail(request, pk):
    _guard(request)
    lang = _lang(request)
    order = get_object_or_404(po.scoped().select_related("supplier"), pk=pk)
    words = WORDS[lang]
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "move":
                to = request.POST.get("to") or ""
                order = po.move(order, to, request.user, lang)
                message = words["moved"].format(number=order.number, status=po.STATUS_LABELS[lang].get(to, to))
            elif action == "details":
                po.update_header(order, {key: request.POST.get(key) for key in request.POST}, request.user, lang)
                message = words["saved"]
            elif action == "add_line":
                item = Item.objects.filter(pk=request.POST.get("item") or 0, active=True).first()
                po.add_line(order, item, request.user, quantity=request.POST.get("quantity"), unit_price=request.POST.get("unit_price"),
                            discount_percent=request.POST.get("discount_percent"), lang=lang)
                message = words["saved"]
            elif action == "remove_line":
                line = get_object_or_404(PurchaseOrderLine, pk=request.POST.get("line") or 0, order=order)
                po.remove_line(line, request.user, lang)
                message = words["saved"]
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, message)
            return redirect(f"{reverse('purchase_orders:detail', args=[order.pk])}?lang={lang}")
        order.refresh_from_db()
    today = timezone.localdate()
    rows = po.quantities(order)
    state = po.state(order, today, rows)
    can_manage = po.can_manage(request.user)
    hidden = set()
    if any(row["received"] or row["drafted"] for row in rows):
        hidden.add(Status.CANCELLED)
    if state == "done":
        hidden.add(Status.CLOSED)
    moves = [(status, po.MOVE_LABELS[lang][status]) for status in po.NEXT.get(order.status, ()) if status not in hidden] if can_manage else []
    from printing.company import company_details
    from printing.share import whatsapp_link

    numbers = po.totals(order)
    listing = "\n".join(f"- {line.item.item_name}: {line.quantity.normalize():f}" for line in numbers["lines"])
    whatsapp = whatsapp_link(order.supplier.whatsapp or order.supplier.phone, words["share_text"].format(
        number=order.number, company=company_details()["name"], lines=listing)) if numbers["lines"] else ""
    return render(request, "orders/po_detail.html", _base(
        request, lang, order=order, rows=rows, numbers=numbers, moves=moves, error=error, state=state, whatsapp=whatsapp,
        items=Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_name")[:500] if can_manage and order.is_open else [],
        editable=can_manage and order.is_open, can_receive=can_manage and order.status == Status.CONFIRMED and any(row["left"] > 0 for row in rows),
        receipts=po.receipts(order)))


def print_order(request, pk):
    from printing.views import WORDS as PRINT_WORDS, _render

    _guard(request)
    lang = _lang(request)
    order = get_object_or_404(po.scoped().select_related("supplier"), pk=pk)
    words, own = PRINT_WORDS[lang], WORDS[lang]
    numbers = po.totals(order)
    lines = [{"label": line.description or line.item.search_label, "quantity": line.quantity, "unit_price": line.unit_price,
              "discount": line.discount, "total": line.amount} for line in numbers["lines"]]
    totals = [(words["subtotal"], numbers["gross"])]
    if numbers["discount"]:
        totals.append((words["discount"], -numbers["discount"]))
    if numbers["tax"]:
        totals.append((words["tax"], numbers["tax"]))
    doc = {"kind": "purchase_order", "title": own["doc"], "number": order.number, "date": order.order_date,
           "status": "posted" if order.status in (Status.SENT, Status.CONFIRMED, Status.CLOSED) else order.status,
           "status_label": po.STATUS_LABELS[lang].get(order.status, ""), "party_label": words["supplier"],
           "party": {"name": order.supplier.name, "phone": order.supplier.phone, "address": order.supplier.address},
           "reference": (own["expected"], order.expected_on.strftime("%Y-%m-%d")) if order.expected_on else None, "lines": lines,
           "has_discounts": any(line["discount"] for line in lines), "totals": totals, "grand_total": numbers["total"],
           "notes": order.notes, "terms": order.terms, "words_amount": numbers["total"]}
    return _render(request, doc, f"/purchases/orders/{pk}/")
