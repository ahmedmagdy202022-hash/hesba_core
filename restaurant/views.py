"""RESTO-001 screens: the tables board, one order, the kitchen ticket, and the tables list."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from master_data.models import Customer, Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from printing.company import company_details
from settings_core.display_labels import choice_label
from settings_core.module_gate import closed_module
from shifts.services import open_shift_for
from staff.models import Employee

from . import services
from .models import DiningTable, KitchenTicket, Order, OrderKind, OrderLine, OrderStatus
from entities import scope as entity_scope


VIEW, TAKE, TABLES = "sales.view_sales_invoices", "sales.create_sales_invoice", "master_data.manage_items"
WORDS = {
    "ar": {
        "page_title": "الطاولات والطلبات", "title": "الطاولات والطلبات", "intro": "دوس على طاولة فاضية تفتح لها طلب، أو على طاولة مشغولة تكمّل طلبها.",
        "free": "فاضية", "busy": "مشغولة", "seats": "كراسي", "minutes": "دقيقة", "open": "افتح", "tables": "الطاولات", "no_tables": "لسه مفيش طاولات. ضيف طاولات الصالة الأول.",
        "add_tables": "ضيف الطاولات", "others": "تيك أواي ودليفري مفتوحة", "none": "مفيش.", "new_takeaway": "تيك أواي جديد", "new_delivery": "دليفري جديد",
        "customer": "العميل", "walk_in": "— عميل نقدي —", "phone": "الموبايل", "address": "العنوان", "notes": "ملاحظات", "guests": "عدد الأفراد", "waiter": "الويتر",
        "back": "الطاولات", "menu": "المنيو", "search": "دوّر في المنيو", "all": "الكل", "no_items": "مفيش أصناف. ضيف أصناف المنيو من شاشة الأصناف.",
        "add": "ضيف", "qty": "الكمية", "note": "ملاحظة للمطبخ", "item": "الصنف", "price": "السعر", "amount": "القيمة", "total": "الإجمالي", "empty": "الطلب فاضي؛ اختار من المنيو.",
        "sent": "راح المطبخ", "new": "جديد", "voided": "ملغي", "remove": "شيل", "void": "إلغاء السطر", "void_confirm": "السطر ده راح المطبخ. تلغيه؟",
        "send": "ابعت للمطبخ", "sent_ok": "اتبعتت تذكرة المطبخ رقم {n}.", "nothing_new": "مفيش أصناف جديدة تتبعت.", "tickets": "تذاكر المطبخ", "reprint": "اطبع تاني",
        "move": "انقل لطاولة", "moved": "اتنقل الطلب.", "cancel": "إلغاء الطلب", "cancel_confirm": "تلغي الطلب كله؟", "cancelled": "اتلغى الطلب.", "reason": "السبب",
        "pay": "الحساب", "cashbox": "الخزنة", "discount": "خصم", "tendered": "المدفوع", "print": "اطبع الإيصال", "pay_btn": "ادفع واقفل الطلب",
        "paid": "اتقفل الطلب {number} بفاتورة {invoice} بإجمالي {total}.", "change": "الباقي للعميل: {change}.", "view_only": "أخذ الطلبات للي عنده صلاحية البيع بس.",
        "closed_order": "الطلب ده مقفول.", "invoice": "الفاتورة", "table": "الطاولة", "kind": "النوع", "status": "الحالة", "opened": "اتفتح",
        "kitchen": "المطبخ", "ticket": "تذكرة", "tables_title": "طاولات الصالة", "name": "الاسم / الرقم", "area": "المكان", "sort": "الترتيب", "active": "شغالة",
        "save": "حفظ", "saved": "اتحفظت الطاولة.", "new_table": "طاولة جديدة", "edit": "تعديل", "bad_number": "اكتب رقم صحيح.",
    },
    "en": {
        "page_title": "Tables & orders", "title": "Tables & orders", "intro": "Tap a free table to open an order, or a busy one to carry on with its order.",
        "free": "Free", "busy": "Busy", "seats": "seats", "minutes": "min", "open": "Open", "tables": "Tables", "no_tables": "No tables yet. Add the hall's tables first.",
        "add_tables": "Add tables", "others": "Open takeaway & delivery", "none": "None.", "new_takeaway": "New takeaway", "new_delivery": "New delivery",
        "customer": "Customer", "walk_in": "— walk-in —", "phone": "Mobile", "address": "Address", "notes": "Notes", "guests": "Guests", "waiter": "Waiter",
        "back": "Tables", "menu": "Menu", "search": "Search the menu", "all": "All", "no_items": "No items. Add the menu items from the items screen.",
        "add": "Add", "qty": "Qty", "note": "Note for the kitchen", "item": "Item", "price": "Price", "amount": "Amount", "total": "Total", "empty": "The order is empty; pick from the menu.",
        "sent": "Sent", "new": "New", "voided": "Void", "remove": "Remove", "void": "Void line", "void_confirm": "This line went to the kitchen. Void it?",
        "send": "Send to kitchen", "sent_ok": "Kitchen ticket {n} sent.", "nothing_new": "Nothing new to send.", "tickets": "Kitchen tickets", "reprint": "Print again",
        "move": "Move to table", "moved": "Order moved.", "cancel": "Cancel order", "cancel_confirm": "Cancel the whole order?", "cancelled": "Order cancelled.", "reason": "Reason",
        "pay": "Bill", "cashbox": "Cashbox", "discount": "Discount", "tendered": "Paid", "print": "Print the receipt", "pay_btn": "Pay and close",
        "paid": "Order {number} closed with invoice {invoice}, total {total}.", "change": "Change due: {change}.", "view_only": "Only users who can sell can take orders.",
        "closed_order": "This order is closed.", "invoice": "Invoice", "table": "Table", "kind": "Type", "status": "Status", "opened": "Opened",
        "kitchen": "Kitchen", "ticket": "Ticket", "tables_title": "Hall tables", "name": "Name / number", "area": "Area", "sort": "Order", "active": "Active",
        "save": "Save", "saved": "Table saved.", "new_table": "New table", "edit": "Edit", "bad_number": "Enter a valid number.",
    },
}
KIND_AR = {OrderKind.DINE_IN: "صالة", OrderKind.TAKEAWAY: "تيك أواي", OrderKind.DELIVERY: "دليفري"}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _pick(model, raw, **filters):
    raw = str(raw or "")
    return model.objects.filter(pk=raw, **filters).first() if raw.isdigit() else None


def _money(raw, words):
    try:
        value = Decimal(str(raw or "0").strip().replace(",", ""))
    except InvalidOperation:
        raise ValidationError(words["bad_number"])
    if not value.is_finite() or value < 0:
        raise ValidationError(words["bad_number"])
    return value


def _kind_label(order, lang):
    return KIND_AR.get(order.kind, order.kind) if lang == "ar" else order.get_kind_display()


def _base(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"],
               "can_take": user_has_permission(request.user, TAKE), "can_tables": user_has_permission(request.user, TABLES),
               "can_report": user_has_permission(request.user, REPORT_VIEW), "can_costing": user_has_permission(request.user, COST_VIEW),
               "links": INSIGHT_WORDS[lang]}
    context.update(extra)
    return context


def _order_url(order, lang):
    return f"{reverse('restaurant:order', args=[order.pk])}?lang={lang}"


def _error(exc, lang):
    from sales.pos import WORDS as POS_WORDS, _error_text
    from settings_core.ui_messages import translate  # I18N-001: engine refusals in the screen's language

    return translate(_error_text(exc, POS_WORDS[lang]), lang)


@require_permission(VIEW)
def board(request):
    lang = _lang(request)
    words = WORDS[lang]
    if request.method == "POST":
        if not user_has_permission(request.user, TAKE):
            raise PermissionDenied("Taking orders needs sales.create_sales_invoice.")
        kind = request.POST.get("kind", OrderKind.DINE_IN)
        try:
            order = services.open_order(
                request.user, kind=kind, table=_pick(DiningTable, request.POST.get("table")), guests=request.POST.get("guests") or 1,
                customer=_pick(Customer, request.POST.get("customer"), active=True), waiter=_pick(Employee, request.POST.get("waiter"), active=True),
                address=request.POST.get("address", ""), phone=request.POST.get("phone", ""), notes=request.POST.get("notes", ""), lang=lang,
                delivery_fee=request.POST.get("delivery_fee"),
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(f"{reverse('restaurant:board')}?lang={lang}")
        return redirect(_order_url(order, lang))
    tables, others = services.board()
    now = timezone.now()
    for row in tables:
        if row["order"]:
            row["minutes"] = int((now - row["order"].opened_at).total_seconds() // 60)
    return render(request, "restaurant/board.html", _base(
        request, tables=tables, others=[{"order": order, "kind_label": _kind_label(order, lang)} for order in others],
        customers=Customer.objects.filter(active=True).order_by("name"), waiters=Employee.objects.filter(active=True),
    ))


def _menu():
    items = Item.objects.filter(active=True).select_related("category").order_by("category__category_code", "item_name")
    categories, seen = [], set()
    for item in items:
        if item.category_id and item.category_id not in seen:
            seen.add(item.category_id)
            categories.append(item.category)
    return items, categories


@require_permission(VIEW)
def order_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    order = get_object_or_404(Order.objects.select_related("table", "customer", "waiter", "invoice"), pk=pk)
    if request.method == "POST":
        if not user_has_permission(request.user, TAKE):
            raise PermissionDenied("Taking orders needs sales.create_sales_invoice.")
        action = request.POST.get("action", "")
        line = _pick(OrderLine, request.POST.get("line"), order=order)
        try:
            if action == "add":
                services.add_item(order, _pick(Item, request.POST.get("item"), active=True), request.user, request.POST.get("quantity") or 1,
                                  request.POST.get("note", ""), lang)
            elif action == "qty" and line:
                services.change_line(line, request.user, quantity=request.POST.get("quantity"), note=request.POST.get("note"), lang=lang)
            elif action == "void" and line:
                services.void_line(line, request.user, lang)
            elif action == "send":
                ticket = services.send_to_kitchen(order, request.user, lang)
                if ticket is None:
                    messages.info(request, words["nothing_new"])
                else:
                    messages.success(request, words["sent_ok"].format(n=ticket.sequence))
                    if request.POST.get("print", "1") == "1":
                        return redirect(f"{reverse('restaurant:ticket', args=[ticket.pk])}?lang={lang}&autoprint=1")
            elif action == "move":
                services.move_table(order, _pick(DiningTable, request.POST.get("table")), request.user, lang)
                messages.success(request, words["moved"])
            elif action == "dispatch":
                services.dispatch(order, _pick(Employee, request.POST.get("driver"), active=True), request.user,
                                  delivery_fee=request.POST.get("delivery_fee"), lang=lang)
            elif action == "cancel":
                services.cancel_order(order, request.user, request.POST.get("reason", ""), lang)
                messages.success(request, words["cancelled"])
                return redirect(f"{reverse('restaurant:board')}?lang={lang}")
            elif action == "pay":
                invoice, change = services.pay(
                    order, request.user, cashbox=_pick(Cashbox, request.POST.get("cashbox"), active=True),
                    discount=_money(request.POST.get("discount"), words), tendered=_money(request.POST.get("tendered"), words),
                    customer=_pick(Customer, request.POST.get("customer"), active=True), lang=lang,
                )
                text = words["paid"].format(number=order.number, invoice=invoice.invoice_number, total=f"{invoice.total_amount:,.2f}")
                if change:
                    text += " " + words["change"].format(change=f"{change:,.2f}")
                messages.success(request, text)
                if request.POST.get("print") == "1" and closed_module("/print/") is None:
                    return redirect(f"/print/sales/{invoice.pk}/?lang={lang}&format=receipt&autoprint=1")
                return redirect(f"{reverse('restaurant:board')}?lang={lang}")
        except ValidationError as exc:
            messages.error(request, _error(exc, lang))
        return redirect(_order_url(order, lang) + ("#menu" if action == "add" else ""))
    items, categories = _menu()
    lines = list(order.lines.select_related("item", "ticket"))
    cashboxes = entity_scope.cashboxes(Cashbox.objects).filter(active=True)
    shift = open_shift_for(request.user)
    free_tables = DiningTable.objects.filter(active=True).exclude(orders__status=OrderStatus.OPEN)
    return render(request, "restaurant/order.html", _base(
        request, order=order, lines=lines, total=order.total, items=items, categories=categories, kind_label=_kind_label(order, lang),
        drivers=Employee.objects.filter(active=True) if order.kind == OrderKind.DELIVERY else [], dw=DELIVERY_WORDS[lang],
        status_label=choice_label(order, "status", lang), is_open=order.status == OrderStatus.OPEN,
        pending=any(not line.ticket_id and not line.voided for line in lines), tickets=order.tickets.all(),
        cashboxes=cashboxes, default_cashbox=(shift.cashbox if shift else None) or cashboxes.filter(is_default=True).first() or cashboxes.first(),
        customers=Customer.objects.filter(active=True).order_by("name"), free_tables=free_tables,
    ))


@require_permission(VIEW)
def ticket(request, pk):
    lang = _lang(request)
    ticket = get_object_or_404(KitchenTicket.objects.select_related("order", "order__table", "order__waiter", "sent_by"), pk=pk)
    return render(request, "restaurant/ticket.html", _base(
        request, ticket=ticket, order=ticket.order, lines=ticket.lines.select_related("item"), kind_label=_kind_label(ticket.order, lang),
        company=company_details(), sent_at=timezone.localtime(ticket.sent_at),
    ))


@require_permission(VIEW)
def tables(request):
    lang = _lang(request)
    words = WORDS[lang]
    error = ""
    form = {"seats": 4, "active": True}
    editing = _pick(DiningTable, request.GET.get("edit") or request.POST.get("pk"))
    if request.method == "POST":
        if not user_has_permission(request.user, TABLES):
            raise PermissionDenied("Changing tables needs master_data.manage_items.")
        form = request.POST
        data = {key: request.POST.get(key, "") for key in ("name", "area", "seats", "sort_order")}
        data["active"] = request.POST.get("active") == "on"
        try:
            services.save_table(data, request.user, editing, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('restaurant:tables')}?lang={lang}")
    elif editing:
        form = {"name": editing.name, "area": editing.area, "seats": editing.seats, "sort_order": editing.sort_order, "active": editing.active}
    return render(request, "restaurant/tables.html", _base(request, rows=DiningTable.objects.all(), form=form, editing=editing, error=error))


REPORT_VIEW, COST_VIEW = "reports.view_all_sales_report", "inventory.view_cost"
DELIVERY_WORDS = {
    "ar": {"fee": "رسوم التوصيل", "driver": "الطيار", "dispatch": "خرج مع الطيار", "change_driver": "غيّر الطيار", "out": "خرج", "at": "الساعة",
           "deliveries": "الدليفري", "deliveries_intro": "طلبات الدليفري المدفوعة لكل طيار: عددها ورسوم التوصيل ووقت التوصيل من خروجه لحد ما اتقفل.",
           "count": "طلبات", "fees": "رسوم التوصيل", "minutes": "متوسط التوصيل (دقيقة)", "no_driver": "من غير طيار"},
    "en": {"fee": "Delivery fee", "driver": "Driver", "dispatch": "Out with the driver", "change_driver": "Change the driver", "out": "Left", "at": "at",
           "deliveries": "Deliveries", "deliveries_intro": "Paid delivery orders per driver: how many, the delivery fees, and the time from leaving to closing.",
           "count": "Orders", "fees": "Delivery fees", "minutes": "Average delivery (min)", "no_driver": "No driver"},
}
INSIGHT_WORDS = {
    "ar": {"costing": "تكلفة الأطباق", "costing_intro": "مكوّنات الطبق الواحد وتكلفتها بمتوسط تكلفة الخامة النهارده، قصاد سعر البيع. ده حساب تقديري للتسعير؛ البيع مش بيخصم خامات من المخزن.",
           "dish": "الطبق", "price": "سعر البيع", "cost": "تكلفة الطبق", "margin": "الهامش", "margin_pct": "نسبة الهامش", "ingredients": "المكوّنات",
           "no_recipe": "لسه مالوش مكوّنات", "edit": "المكوّنات", "ingredient": "الخامة", "quantity": "الكمية للطبق", "save": "حفظ", "saved": "اتحفظت المكوّنات.",
           "back": "تكلفة الأطباق", "add_row": "سطر زيادة", "report": "تقرير المطعم",
           "report_intro": "الطلبات المدفوعة اللي فاتورتها مترحّلة. قيمة الطلب صافي البيع (قبل الضريبة وبعد المرتجعات)، وقيمة الطبق بسعر المنيو قبل خصم الطلب.",
           "from": "من", "to": "لحد", "go": "اعرض", "orders": "طلبات", "sales": "المبيعات", "average": "متوسط الطلب", "guests": "أفراد في الصالة",
           "minutes": "دقيقة متوسط القعدة", "by_hour": "حسب الساعة", "hour": "الساعة", "peak": "أزحم ساعة", "top": "الأكثر مبيعًا", "qty": "الكمية",
           "tables": "الطاولات", "table": "الطاولة", "per_day": "طلب في اليوم", "stay": "متوسط القعدة (دقيقة)", "waiters": "الويترز", "waiter": "الويتر",
           "no_waiter": "من غير ويتر", "kinds": "نوع الطلب", "kind": "النوع", "waste": "الهالك والفاقد", "waste_intro": "اللي اتشال من المخزن بتسوية في الفترة دي، بسببها.",
           "item": "الصنف", "date": "التاريخ", "reason": "السبب", "value": "القيمة", "no_waste": "مفيش هالك متسجّل في الفترة دي.", "nothing": "مفيش طلبات مدفوعة في الفترة دي.",
           "bad_period": "الفترة مش مظبوطة؛ اتعرض الشهر ده.", "delivery_fee": "رسوم التوصيل", "dine_in": "صالة", "takeaway": "تيك أواي", "delivery": "دليفري", "total": "الإجمالي"},
    "en": {"costing": "Dish costs", "costing_intro": "What one plate uses and what it costs at today's average ingredient cost, against its price. An estimate for pricing; selling takes nothing out of stock.",
           "dish": "Dish", "price": "Price", "cost": "Plate cost", "margin": "Margin", "margin_pct": "Margin %", "ingredients": "Ingredients",
           "no_recipe": "No ingredients yet", "edit": "Ingredients", "ingredient": "Ingredient", "quantity": "Quantity per plate", "save": "Save", "saved": "Ingredients saved.",
           "back": "Dish costs", "add_row": "Another row", "report": "Restaurant report",
           "report_intro": "Paid orders whose invoice is posted. An order's value is its net sales (before tax, after returns); a dish's is at menu price, before the order's discount.",
           "from": "From", "to": "To", "go": "Show", "orders": "Orders", "sales": "Sales", "average": "Average order", "guests": "Guests seated",
           "minutes": "min average stay", "by_hour": "By hour", "hour": "Hour", "peak": "Busiest hour", "top": "Best sellers", "qty": "Qty",
           "tables": "Tables", "table": "Table", "per_day": "orders a day", "stay": "Average stay (min)", "waiters": "Waiters", "waiter": "Waiter",
           "no_waiter": "No waiter", "kinds": "Order type", "kind": "Type", "waste": "Waste and losses", "waste_intro": "What was taken out of stock by adjustment in this period, with its reason.",
           "item": "Item", "date": "Date", "reason": "Reason", "value": "Value", "no_waste": "No waste recorded in this period.", "nothing": "No paid orders in this period.",
           "bad_period": "The period was not valid; showing this month.", "delivery_fee": "Delivery fee", "dine_in": "Dine in", "takeaway": "Takeaway", "delivery": "Delivery", "total": "Total"},
}


def _dishes():
    return Item.objects.filter(active=True, is_stock_tracked=False).order_by("item_name")


@require_permission(COST_VIEW)
def menu_costing(request):
    from . import insights

    lang = _lang(request)
    words = INSIGHT_WORDS[lang]
    rows = insights.menu_costing(list(_dishes()))
    return render(request, "restaurant/costing.html", _base(request, page_title=words["costing"], iw=words, rows=rows,
                                                          can_edit=user_has_permission(request.user, TABLES)))


@require_permission(COST_VIEW)
def dish_ingredients(request, pk):
    from . import insights

    lang = _lang(request)
    words = INSIGHT_WORDS[lang]
    dish = get_object_or_404(_dishes(), pk=pk)
    ingredients = Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_name")
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, TABLES):
            raise PermissionDenied("Changing a dish's ingredients needs master_data.manage_items.")
        lines = [(ingredients.filter(pk=raw).first() if str(raw).isdigit() else None, qty)
                 for raw, qty in zip(request.POST.getlist("ingredient"), request.POST.getlist("quantity"))]
        try:
            insights.save_ingredients(dish, lines, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('restaurant:costing')}?lang={lang}")
    current = list(dish.dish_ingredients.select_related("ingredient"))
    rows = [{"ingredient": row.ingredient_id, "quantity": row.quantity} for row in current] + [{"ingredient": None, "quantity": ""}] * 3
    if request.method == "POST":
        rows = [{"ingredient": int(raw) if str(raw).isdigit() else None, "quantity": qty}
                for raw, qty in zip(request.POST.getlist("ingredient"), request.POST.getlist("quantity"))]
    return render(request, "restaurant/dish_ingredients.html", _base(request, page_title=dish.item_name, iw=words, dish=dish, rows=rows,
                                                                   ingredients=ingredients, cost=insights.plate_cost(dish, current), error=error,
                                                                   can_edit=user_has_permission(request.user, TABLES)))


def _period(request, words):
    from datetime import date

    today = timezone.localdate()
    first, last, note = today.replace(day=1), today, ""
    if request.GET.get("from") or request.GET.get("to"):
        try:
            first = date.fromisoformat(request.GET.get("from")) if request.GET.get("from") else first
            last = date.fromisoformat(request.GET.get("to")) if request.GET.get("to") else last
            if first > last or (last - first).days > 366:
                raise ValueError
        except ValueError:
            first, last, note = today.replace(day=1), today, words["bad_period"]
    return first, last, note


@require_permission(REPORT_VIEW)
def report(request):
    from . import insights

    lang = _lang(request)
    words = INSIGHT_WORDS[lang]
    first, last, note = _period(request, words)
    data = insights.period_report(first, last)
    for row in data["kinds"]:
        row["label"] = words.get(row["kind"], row["kind"])
    waste_rows, waste_total = insights.waste(first, last)
    return render(request, "restaurant/report.html", _base(request, page_title=words["report"], iw=words, first=first, last=last, note=note,
                                                         deliveries=insights.deliveries(first, last), dw=DELIVERY_WORDS[lang],
                                                         today=timezone.localdate(), waste=waste_rows, waste_total=waste_total,
                                                         can_cost=user_has_permission(request.user, COST_VIEW), **data))
