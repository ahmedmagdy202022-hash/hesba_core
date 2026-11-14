"""RESTO-001 screens: the tables board, one order, the kitchen ticket, and the tables list."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer, Item
from permissions.decorators import require_any_permission, require_permission
from permissions.services import user_has_permission
from printing.company import company_details
from settings_core.display_labels import choice_label
from settings_core.module_gate import closed_module
from shifts.gate import require_open_shift, shift_missing
from shifts.services import open_shift_for
from staff.models import Employee

from . import branches, services
from .models import DiningTable, KitchenTicket, Order, OrderKind, OrderLine, OrderStatus
from entities.services import is_multi_entity


VIEW, TAKE, TABLES = "sales.view_sales_invoices", "sales.create_sales_invoice", "master_data.manage_items"
#: HG-048: the floor (tables, orders, kitchen tickets) without selling: a waiter holds this and not TAKE,
#: so they never take money, see the invoices or pick a cashbox.
TAKE_ORDERS = "restaurant.take_orders"
FLOOR = (VIEW, TAKE_ORDERS)


def _takes_orders(user):
    return user_has_permission(user, TAKE) or user_has_permission(user, TAKE_ORDERS)
WORDS = {
    "ar": {
        "page_title": "الطاولات والطلبات", "title": "الطاولات والطلبات", "intro": "دوس على طاولة فاضية تفتح لها طلب، أو على طاولة مشغولة تكمّل طلبها.",
        "free": "فاضية", "busy": "مشغولة", "seats": "كراسي", "minutes": "دقيقة", "open": "افتح", "tables": "الطاولات", "no_tables": "لسه مفيش طاولات. ضيف طاولات الصالة الأول.",
        "add_tables": "ضيف الطاولات", "others": "تيك أواي ودليفري مفتوحة", "none": "مفيش.", "new_takeaway": "تيك أواي جديد", "new_delivery": "دليفري جديد",
        "customer": "العميل", "walk_in": "— عميل نقدي —", "phone": "الموبايل", "address": "العنوان", "notes": "ملاحظات", "guests": "عدد الأفراد", "waiter": "الويتر",
        "back": "الطاولات", "menu": "المنيو", "search": "دوّر في المنيو", "all": "الكل", "no_items": "مفيش أصناف. ضيف أصناف المنيو من شاشة الأصناف.",
        "add": "ضيف", "qty": "الكمية", "note": "ملاحظة للمطبخ", "item": "الصنف", "price": "السعر", "amount": "القيمة", "total": "الإجمالي", "empty": "الطلب فاضي؛ اختار من المنيو.",
        "sent": "راح المطبخ", "new": "جديد", "voided": "ملغي", "remove": "شيل", "void": "إلغاء السطر", "void_confirm": "السطر ده راح المطبخ. تلغيه؟", "void_reason": "سبب الإلغاء",
        "send": "ابعت للمطبخ", "sent_ok": "اتبعتت تذكرة المطبخ رقم {n}.", "nothing_new": "مفيش أصناف جديدة تتبعت.", "tickets": "تذاكر المطبخ", "reprint": "اطبع تاني",
        "move": "انقل لطاولة", "moved": "اتنقل الطلب.", "dispatched": "الطلب خرج مع الطيار.", "cancel": "إلغاء الطلب", "cancel_confirm": "تلغي الطلب كله؟", "cancelled": "اتلغى الطلب.", "reason": "السبب",
        "pay": "الحساب", "cashbox": "الخزنة", "discount": "خصم", "tendered": "المدفوع", "print": "اطبع الإيصال", "pay_btn": "ادفع واقفل الطلب",
        "paid": "اتقفل الطلب {number} بفاتورة {invoice} بإجمالي {total}.", "change": "الباقي للعميل: {change}.", "view_only": "أخذ الطلبات للي عنده صلاحية البيع بس.",
        "closed_order": "الطلب ده مقفول.", "invoice": "الفاتورة", "table": "الطاولة", "kind": "النوع", "status": "الحالة", "opened": "اتفتح",
        "kitchen": "المطبخ", "ticket": "تذكرة", "tables_title": "طاولات الصالة", "name": "الاسم / الرقم", "area": "المكان", "sort": "الترتيب", "active": "شغالة",
        "save": "حفظ", "saved": "اتحفظت الطاولة.", "new_table": "طاولة جديدة", "edit": "تعديل", "bad_number": "اكتب رقم صحيح.",
        "ready": "جاهز", "ready_n": "جاهز {n}", "void_why": "السبب", "kitchen_screen": "شاشة المطبخ", "stations_title": "محطات المطبخ",
        "modifiers_title": "الإضافات والاختيارات",
        "sent_many": "اتبعتت {count} للمطبخ ({stations}).",
        "branch": "الفرع", "choose_branch": "اختار الفرع", "no_branch_tables": "لسه مفيش طاولات في الفرع ده.",
    },
    "en": {
        "page_title": "Tables & orders", "title": "Tables & orders", "intro": "Tap a free table to open an order, or a busy one to carry on with its order.",
        "free": "Free", "busy": "Busy", "seats": "seats", "minutes": "min", "open": "Open", "tables": "Tables", "no_tables": "No tables yet. Add the hall's tables first.",
        "add_tables": "Add tables", "others": "Open takeaway & delivery", "none": "None.", "new_takeaway": "New takeaway", "new_delivery": "New delivery",
        "customer": "Customer", "walk_in": "— walk-in —", "phone": "Mobile", "address": "Address", "notes": "Notes", "guests": "Guests", "waiter": "Waiter",
        "back": "Tables", "menu": "Menu", "search": "Search the menu", "all": "All", "no_items": "No items. Add the menu items from the items screen.",
        "add": "Add", "qty": "Qty", "note": "Note for the kitchen", "item": "Item", "price": "Price", "amount": "Amount", "total": "Total", "empty": "The order is empty; pick from the menu.",
        "sent": "Sent", "new": "New", "voided": "Void", "remove": "Remove", "void": "Void line", "void_confirm": "This line went to the kitchen. Void it?", "void_reason": "Why it is voided",
        "send": "Send to kitchen", "sent_ok": "Kitchen ticket {n} sent.", "nothing_new": "Nothing new to send.", "tickets": "Kitchen tickets", "reprint": "Print again",
        "move": "Move to table", "moved": "Order moved.", "dispatched": "The order is out with the driver.", "cancel": "Cancel order", "cancel_confirm": "Cancel the whole order?", "cancelled": "Order cancelled.", "reason": "Reason",
        "pay": "Bill", "cashbox": "Cashbox", "discount": "Discount", "tendered": "Paid", "print": "Print the receipt", "pay_btn": "Pay and close",
        "paid": "Order {number} closed with invoice {invoice}, total {total}.", "change": "Change due: {change}.", "view_only": "Only users who can sell can take orders.",
        "closed_order": "This order is closed.", "invoice": "Invoice", "table": "Table", "kind": "Type", "status": "Status", "opened": "Opened",
        "kitchen": "Kitchen", "ticket": "Ticket", "tables_title": "Hall tables", "name": "Name / number", "area": "Area", "sort": "Order", "active": "Active",
        "save": "Save", "saved": "Table saved.", "new_table": "New table", "edit": "Edit", "bad_number": "Enter a valid number.",
        "ready": "Ready", "ready_n": "{n} ready", "void_why": "Reason", "kitchen_screen": "Kitchen screen", "stations_title": "Kitchen stations",
        "modifiers_title": "Add-ons and choices",
        "sent_many": "{count} sent to the kitchen ({stations}).",
        "branch": "Branch", "choose_branch": "Choose the branch", "no_branch_tables": "No tables in this branch yet.",
    },
}
KIND_AR = {OrderKind.DINE_IN: "صالة", OrderKind.TAKEAWAY: "تيك أواي", OrderKind.DELIVERY: "دليفري"}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _pick(model, raw, **filters):
    raw = str(raw or "")
    return model.objects.filter(pk=raw, **filters).first() if raw.isdigit() else None


def _own_cashbox(raw, order):
    """The cashbox a bill is paid into: only one the pay form itself lists (the order's entity's, active, never a held kind; R12)."""

    raw = str(raw or "")
    return branches.cashboxes(order).filter(pk=raw).first() if raw.isdigit() else None


def _table(raw):
    """A table by a pk as a form sends it, in the entity being worked in only (R12): another branch's is as if none was sent."""

    raw = str(raw or "")
    return branches.tables().filter(pk=raw).first() if raw.isdigit() else None


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
               "can_take": _takes_orders(request.user), "can_sell": user_has_permission(request.user, TAKE),
               "can_see_invoices": user_has_permission(request.user, VIEW), "can_tables": user_has_permission(request.user, TABLES),
               "can_report": user_has_permission(request.user, REPORT_VIEW), "can_costing": user_has_permission(request.user, COST_VIEW),
               "can_kds": user_has_permission(request.user, TAKE_ORDERS), "links": INSIGHT_WORDS[lang]}
    context.update(extra)
    return context


def _order_url(order, lang):
    return f"{reverse('restaurant:order', args=[order.pk])}?lang={lang}"


def _error(exc, lang):
    from sales.pos import WORDS as POS_WORDS, _error_text
    from settings_core.ui_messages import translate  # I18N-001: engine refusals in the screen's language

    return translate(_error_text(exc, POS_WORDS[lang]), lang)


def _ready_count(order):
    """R4: how many of an order's live lines the kitchen marked ready (there is no «served» step yet, so this stays until the bill)."""

    return sum(1 for line in order.lines.all() if line.ready_at and not line.voided and not line.parent_id)  # R1a: dishes, not their add-ons


@require_any_permission(*FLOOR)
def board(request):
    lang = _lang(request)
    words = WORDS[lang]
    if request.method == "POST":
        if not _takes_orders(request.user):
            raise PermissionDenied("Taking orders needs sales.create_sales_invoice or restaurant.take_orders.")
        kind = request.POST.get("kind", OrderKind.DINE_IN)
        entity = None
        if kind != OrderKind.DINE_IN and branches.group_mode():  # R12: across the group, a takeaway or a delivery names its branch
            entity = branches.pick(request.user, request.POST.get("entity"))
            if entity is None:
                messages.error(request, services.MESSAGES[lang]["branch"])
                return redirect(f"{reverse('restaurant:board')}?lang={lang}")
        try:
            order = services.open_order(
                request.user, kind=kind, table=_table(request.POST.get("table")), guests=request.POST.get("guests") or 1, entity=entity,
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
            row["ready"] = _ready_count(row["order"])
            row["ready_label"] = words["ready_n"].format(n=row["ready"])
    ready = {order.pk: _ready_count(order) for order in others}
    grouped = branches.group_mode()
    groups = [{**choice, "tables": [row for row in tables if row["table"].entity_id == choice["pk"]]} for choice in branches.choices(request.user, lang)] if grouped else []
    return render(request, "restaurant/board.html", _base(
        request, tables=tables, others=[{"order": order, "kind_label": _kind_label(order, lang), "ready": ready[order.pk],
                                         "ready_label": words["ready_n"].format(n=ready[order.pk]),
                                         "branch": order.entity.display(lang) if grouped else ""} for order in others],
        customers=Customer.objects.filter(active=True).order_by("name"), waiters=Employee.objects.filter(active=True),
        branch_groups=groups, branch_choices=branches.choices(request.user, lang) if grouped else [],
    ))


def _raw_ingredient_ids():
    """RS-02: items the waiter's menu keeps out of the way: an ingredient of some dish that is not itself a dish (no recipe of
    its own) and has never been put on an order. The recipe link is the only existing signal that tells rice from a dish, and it
    is a guess, so these are never removed: the order page leaves them off the «الكل» view and still shows them under their own
    category and in the search, so an ingredient that is also sold as it is (bread, milk) can always be ordered. A flag on the
    item would do this exactly (the planned restaurants task)."""

    from .models import DishIngredient

    ingredients = set(DishIngredient.objects.values_list("ingredient_id", flat=True))
    if not ingredients:
        return set()
    dishes = set(DishIngredient.objects.values_list("dish_id", flat=True))
    ordered = set(OrderLine.objects.filter(item_id__in=ingredients).values_list("item_id", flat=True))
    return ingredients - dishes - ordered


def _menu():
    items = Item.objects.filter(active=True).exclude(item_code=services.FEE_ITEM_CODE).select_related("category").order_by("category__category_code", "item_name")
    categories, seen = [], set()
    for item in items:
        if item.category_id and item.category_id not in seen:
            seen.add(item.category_id)
            categories.append(item.category)
    return items, categories


@require_any_permission(*FLOOR)
def order_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    order = get_object_or_404(branches.orders(Order.objects.select_related("table", "customer", "waiter", "invoice", "entity")), pk=pk)  # R12: another branch's is a 404
    if request.method == "POST":
        if not _takes_orders(request.user):
            raise PermissionDenied("Taking orders needs sales.create_sales_invoice or restaurant.take_orders.")
        action = request.POST.get("action", "")
        if action in SELLING_ACTIONS and not user_has_permission(request.user, TAKE):
            raise PermissionDenied("Billing, dispatching or cancelling an order needs sales.create_sales_invoice.")
        line = _pick(OrderLine, request.POST.get("line"), order=order)
        try:
            if action == "add":
                from .views_modifiers import posted_options

                services.add_item(order, _pick(Item, request.POST.get("item"), active=True), request.user, request.POST.get("quantity") or 1,
                                  request.POST.get("note", ""), lang, options=posted_options(request.POST))  # R1a: the sheet's choices
            elif action == "qty" and line:
                services.change_line(line, request.user, quantity=request.POST.get("quantity"), note=request.POST.get("note"), lang=lang)
            elif action == "void" and line:
                services.void_line(line, request.user, lang, reason=request.POST.get("reason", ""))
            elif action == "send":
                ticket = services.send_to_kitchen(order, request.user, lang)
                if ticket is None:
                    messages.info(request, words["nothing_new"])
                else:
                    batch = ticket.batch
                    if len(batch) == 1:
                        messages.success(request, words["sent_ok"].format(n=ticket.sequence))
                    else:  # R4: one ticket per station, each printed on its own page
                        names = ("، " if lang == "ar" else ", ").join(dict.fromkeys(t.station.label(lang) if t.station_id else words["kitchen"] for t in batch))
                        count = ("تذكرتين" if len(batch) == 2 else f"{len(batch)} تذاكر") if lang == "ar" else f"{len(batch)} tickets"
                        messages.success(request, words["sent_many"].format(count=count, stations=names))
                    if request.POST.get("print", "1") == "1":
                        upto = f"&upto={batch[-1].sequence}" if len(batch) > 1 else ""
                        return redirect(f"{reverse('restaurant:ticket', args=[ticket.pk])}?lang={lang}&autoprint=1{upto}")
            elif action == "move":
                services.move_table(order, _table(request.POST.get("table")), request.user, lang)
                messages.success(request, words["moved"])
            elif action == "dispatch":
                services.dispatch(order, _pick(Employee, request.POST.get("driver"), active=True), request.user,
                                  delivery_fee=request.POST.get("delivery_fee"), lang=lang)
                messages.success(request, words["dispatched"])
            elif action == "cancel":
                services.cancel_order(order, request.user, request.POST.get("reason", ""), lang)
                messages.success(request, words["cancelled"])
                return redirect(f"{reverse('restaurant:board')}?lang={lang}")
            elif action == "pay":
                require_open_shift(request.user, lang)  # Restaurants R13: the till's own rule (AN4), before anything is made or posted
                invoice, change = services.pay(
                    order, request.user, cashbox=_own_cashbox(request.POST.get("cashbox"), order),
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
    from . import modifiers
    from .views_modifiers import MODIFIER_WORDS, menu_entries, sheets

    items, categories = _menu()
    raw_ids = _raw_ingredient_ids()
    lines = list(order.lines.select_related("item", "ticket"))
    choice_sheets = sheets(items, lang) if order.status == OrderStatus.OPEN and _takes_orders(request.user) else []  # R1a
    cashboxes = branches.cashboxes(order)  # R12: the order's branch's, whatever entity the biller works in
    shift = open_shift_for(request.user)
    shift_box = shift.cashbox if shift and cashboxes.filter(pk=shift.cashbox_id).exists() else None
    free_tables = DiningTable.objects.filter(active=True, entity_id=order.entity_id).exclude(orders__status=OrderStatus.OPEN)
    return render(request, "restaurant/order.html", _base(
        request, order=order, lines=lines, total=order.total, items=items, raw_ids=raw_ids, categories=categories, kind_label=_kind_label(order, lang),
        families=[{"line": line, "addons": addons} for line, addons in modifiers.grouped(lines)], sheets=choice_sheets,
        sheet_items={sheet["item"].pk for sheet in choice_sheets}, mw=MODIFIER_WORDS[lang],
        **dict(zip(("menu", "size_sheets"), menu_entries(items, {sheet["item"].pk for sheet in choice_sheets}, lang))),
        drivers=Employee.objects.filter(active=True) if order.kind == OrderKind.DELIVERY else [], dw=DELIVERY_WORDS[lang],
        status_label=choice_label(order, "status", lang), is_open=order.status == OrderStatus.OPEN,
        pending=any(not line.ticket_id and not line.voided for line in lines), tickets=order.tickets.select_related("station"),
        cashboxes=cashboxes, default_cashbox=shift_box or cashboxes.filter(is_default=True).first() or cashboxes.first(),
        branch_label=order.entity.display(lang) if is_multi_entity() else "",
        shift_missing=shift_missing(request.user),
        customers=Customer.objects.filter(active=True).order_by("name"), free_tables=free_tables,
    ))


#: What only someone who sells may do on an order: take the money, send it out with a driver (it carries a fee), cancel it.
SELLING_ACTIONS = frozenset({"pay", "dispatch", "cancel"})


@require_any_permission(*FLOOR)
def ticket(request, pk):
    """The kitchen ticket to print. ``upto`` (from a send that made several, one per station) adds the tickets up to that
    sequence: each is its own page, so each station's printer takes its own."""

    lang = _lang(request)
    first = get_object_or_404(branches.tickets(KitchenTicket.objects.select_related("order", "order__table", "order__waiter", "sent_by", "station")), pk=pk)
    upto = request.GET.get("upto", "")
    tickets = [first]
    if upto.isdigit() and int(upto) > first.sequence:
        tickets += list(first.order.tickets.select_related("order", "order__table", "order__waiter", "sent_by", "station")
                        .filter(sequence__gt=first.sequence, sequence__lte=int(upto)))
    from .modifiers import addon_label, grouped

    # R1a (HG-109): each dish with its free choices and its add-ons printed under it; an add-on is never a dish of its own
    cards = [{"ticket": one, "dishes": [{"line": line, "addons": [{"line": addon, "label": addon_label(addon)} for addon in addons]}
                                        for line, addons in grouped(one.lines.select_related("item"))],
              "sent_at": timezone.localtime(one.sent_at), "station": one.station.label(lang) if one.station_id else ""} for one in tickets]
    return render(request, "restaurant/ticket.html", _base(
        request, ticket=first, order=first.order, cards=cards, kind_label=_kind_label(first.order, lang), company=company_details(),
    ))


@require_permission(VIEW)
def tables(request):
    lang = _lang(request)
    words = WORDS[lang]
    error = ""
    form = {"seats": 4, "active": True}
    raw = str(request.GET.get("edit") or request.POST.get("pk") or "")
    editing = get_object_or_404(branches.tables(), pk=int(raw)) if raw.isdigit() else None  # R12: another branch's table is a 404
    grouped = branches.group_mode()
    if request.method == "POST":
        if not user_has_permission(request.user, TABLES):
            raise PermissionDenied("Changing tables needs master_data.manage_items.")
        form = request.POST
        data = {key: request.POST.get(key, "") for key in ("name", "area", "seats", "sort_order")}
        data["active"] = request.POST.get("active") == "on"
        try:
            entity = branches.pick(request.user, request.POST.get("entity")) if grouped else None  # a branch works in its own
            if grouped and entity is None and editing is None:
                raise ValidationError(services.MESSAGES[lang]["branch"])
            services.save_table(data, request.user, editing, lang, entity=entity)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('restaurant:tables')}?lang={lang}")
    elif editing:
        form = {"name": editing.name, "area": editing.area, "seats": editing.seats, "sort_order": editing.sort_order, "active": editing.active,
                "entity": editing.entity_id}
    chosen = str(form.get("entity") or "")
    return render(request, "restaurant/tables.html", _base(request, rows=branches.tables(DiningTable.objects.select_related("entity")), form=form, editing=editing,
                                                         error=error, branch_choices=branches.choices(request.user, lang) if grouped else [], chosen_branch=chosen))


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
    "ar": {"costing": "تكلفة الأطباق", "costing_intro": "مكوّنات الطبق الواحد وتكلفتها بمتوسط تكلفة الخامة النهارده، قصاد سعر البيع. ده حساب تقديري للتسعير. الطبق اللي عليه «بيتخصم من المخزن» بيتحضّر من مكوناته قبل ما يتباع؛ غيره البيع مش بيخصم خامات.",
           "dish": "الطبق", "price": "سعر البيع", "cost": "تكلفة الطبق", "margin": "الهامش", "margin_pct": "نسبة الهامش", "ingredients": "المكوّنات",
           "no_recipe": "لسه مالوش مكوّنات", "edit": "المكوّنات", "ingredient": "الخامة", "quantity": "الكمية للطبق", "save": "حفظ", "saved": "اتحفظت المكوّنات.",
           "back": "تكلفة الأطباق", "add_row": "سطر زيادة", "report": "تقرير المطعم",
           "others": "فيه كمان فواتير بيع من غير طلب مطعم (فواتير شركات أو من شاشة البيع) بـ {other}، فكل مبيعات الفترة {all}، وده رقم لوحة القيادة.",
           "report_intro": "الطلبات المدفوعة اللي فاتورتها مترحّلة. قيمة الطلب صافي البيع (قبل الضريبة وبعد المرتجعات)، وقيمة الطبق بسعر المنيو قبل خصم الطلب.",
           "from": "من", "to": "لحد", "go": "اعرض", "orders": "طلبات", "sales": "المبيعات", "average": "متوسط الطلب", "guests": "أفراد في الصالة",
           "minutes": "دقيقة متوسط القعدة", "by_hour": "حسب الساعة", "hour": "الساعة", "peak": "أزحم ساعة", "top": "الأكثر مبيعًا", "qty": "الكمية",
           "tables": "الطاولات", "table": "الطاولة", "per_day": "طلب في اليوم", "stay": "متوسط القعدة (دقيقة)", "waiters": "الويترز", "waiter": "الويتر",
           "no_waiter": "من غير ويتر", "kinds": "نوع الطلب", "kind": "النوع", "waste": "الهالك والفاقد", "waste_intro": "اللي اتشال من المخزن بتسوية في الفترة دي، بسببها.",
           "item": "الصنف", "date": "التاريخ", "reason": "السبب", "value": "القيمة", "no_waste": "مفيش هالك متسجّل في الفترة دي.", "nothing": "مفيش طلبات مدفوعة في الفترة دي.",
           "bad_period": "الفترة مش مظبوطة؛ اتعرض الشهر ده.", "delivery_fee": "رسوم التوصيل", "dine_in": "صالة", "takeaway": "تيك أواي", "delivery": "دليفري", "total": "الإجمالي"},
    "en": {"costing": "Dish costs", "costing_intro": "What one plate uses and what it costs at today's average ingredient cost, against its price. An estimate for pricing. A dish marked “Taken from stock” is made from its ingredients before it sells; selling any other dish takes nothing out of stock.",
           "dish": "Dish", "price": "Price", "cost": "Plate cost", "margin": "Margin", "margin_pct": "Margin %", "ingredients": "Ingredients",
           "no_recipe": "No ingredients yet", "edit": "Ingredients", "ingredient": "Ingredient", "quantity": "Quantity per plate", "save": "Save", "saved": "Ingredients saved.",
           "back": "Dish costs", "add_row": "Another row", "report": "Restaurant report",
           "others": "There are also sales invoices without a restaurant order (company invoices or from the sales screen) worth {other}, so the period's sales are {all}, the dashboard figure.",
           "report_intro": "Paid orders whose invoice is posted. An order's value is its net sales (before tax, after returns); a dish's is at menu price, before the order's discount.",
           "from": "From", "to": "To", "go": "Show", "orders": "Orders", "sales": "Sales", "average": "Average order", "guests": "Guests seated",
           "minutes": "min average stay", "by_hour": "By hour", "hour": "Hour", "peak": "Busiest hour", "top": "Best sellers", "qty": "Qty",
           "tables": "Tables", "table": "Table", "per_day": "orders a day", "stay": "Average stay (min)", "waiters": "Waiters", "waiter": "Waiter",
           "no_waiter": "No waiter", "kinds": "Order type", "kind": "Type", "waste": "Waste and losses", "waste_intro": "What was taken out of stock by adjustment in this period, with its reason.",
           "item": "Item", "date": "Date", "reason": "Reason", "value": "Value", "no_waste": "No waste recorded in this period.", "nothing": "No paid orders in this period.",
           "bad_period": "The period was not valid; showing this month.", "delivery_fee": "Delivery fee", "dine_in": "Dine in", "takeaway": "Takeaway", "delivery": "Delivery", "total": "Total"},
}


def _dishes():
    """The menu's dishes: services, and (RESTO-004) the stock dishes made in the kitchen from a recipe."""

    from django.db.models import Q

    from . import kitchen

    return (Item.objects.filter(Q(is_stock_tracked=False) | Q(pk__in=kitchen.kitchen_dish_ids()), active=True)
            .exclude(item_code=services.FEE_ITEM_CODE).order_by("item_name"))


KITCHEN_WORDS = {
    "ar": {"title": "الخصم من المخزن", "off": "الطبق ده خدمة: بيعه مش بيخصم خامات من المخزن، والتكلفة اللي فوق تقديرية.",
           "on": "الطبق ده بيتحضّر في المطبخ: كل طبق بيتعمل من مكوّناته (بتتخصم من المخزن بتكلفتها) قبل ما يتباع، وتكلفته الحقيقية بتدخل في تكلفة المبيعات.",
           "switch": "اخصم الخامات لما الطبق يتباع", "switch_hint": "لو الطبق اتباع قبل كده، هيتعمل له صنف مخزني جديد بنفس الاسم والسعر يحل محله في المنيو، والقديم يفضل لفواتيره.",
           "switched": "اتحوّل الطبق: من دلوقتي بيتحضّر من مكوّناته قبل ما يتباع.", "prepare": "حضّر كمية دلوقتي", "quantity": "عدد الأطباق",
           "prepared": "اتحضّر {quantity} طبق.", "in_stock": "جاهز في المخزن", "auto": "لو الجاهز مش كفاية وقت الحساب: اللي عنده صلاحية تسوية المخزون الناقص بيتحضّر له لوحده، والكاشير بيبيع من الجاهز بس. ولو خامة ناقصة، الحساب بيقف ويقول هي إيه.",
           "badge": "بيتخصم من المخزن",
           # HG-107: «إلغاء التحضير»
           "preps": "آخر التحضيرات", "no_preps": "لسه مفيش تحضيرات للطبق ده.", "plates": "طبق", "per_plate": "تكلفة الطبق", "by": "سجّله",
           "cancel": "إلغاء التحضير", "reason": "سبب الإلغاء", "cancelled_state": "اتلغى", "why": "السبب",
           "cancel_note": "الإلغاء بيخرّج أطباق التحضير من المخزن ويرجّع كل مكوّناتها بنفس التكلفة اللي خرجت بيها. لو في أطباق منه خرجت من المخزن بعده، مينفعش يتلغي.",
           "cancelled": "اتلغى التحضير: الأطباق خرجت من المخزن وكل المكوّنات رجعت بتكلفتها.", "waiting": "طلب إلغاء التحضير ده مستني موافقة."},
    "en": {"title": "Taking it out of stock", "off": "This dish is a service: selling it takes no ingredients out of stock, and the cost above is an estimate.",
           "on": "This dish is made in the kitchen: each plate is made from its ingredients (taken out of stock at their cost) before it is sold, and its real cost goes into the cost of sales.",
           "switch": "Take the ingredients out of stock when it sells", "switch_hint": "If the dish was sold before, a new stock item with the same name and price takes its place on the menu; the old one stays for its invoices.",
           "switched": "Switched: from now on the dish is made from its ingredients before it is sold.", "prepare": "Prepare a quantity now", "quantity": "Plates",
           "prepared": "{quantity} plates prepared.", "in_stock": "Ready in stock", "auto": "If not enough is ready at the bill: for someone who may adjust stock the rest is made on the spot; a cashier sells only what is ready. If an ingredient is short, the bill stops and says which.",
           "badge": "Taken from stock",
           "preps": "Recent preparations", "no_preps": "No preparations of this dish yet.", "plates": "plates", "per_plate": "Cost a plate", "by": "By",
           "cancel": "Cancel the preparation", "reason": "Reason for cancelling", "cancelled_state": "Cancelled", "why": "Reason",
           "cancel_note": "Cancelling takes the preparation's plates out of stock and brings every ingredient back at the cost it left at. Once some of its plates have left stock since, it cannot be cancelled.",
           "cancelled": "Preparation cancelled: the plates left stock and every ingredient came back at its cost.", "waiting": "Cancelling this preparation is waiting for approval."},
}


@require_permission(COST_VIEW)
def menu_costing(request):
    from . import insights

    lang = _lang(request)
    words = INSIGHT_WORDS[lang]
    from . import kitchen

    dishes = list(_dishes())
    rows = insights.menu_costing(dishes)
    return render(request, "restaurant/costing.html", _base(request, page_title=words["costing"], iw=words, rows=rows,
                                                          kitchen_ids=kitchen.kitchen_dish_ids(dishes), kw=KITCHEN_WORDS[lang],
                                                          can_edit=user_has_permission(request.user, TABLES)))


@require_permission(COST_VIEW)
def dish_ingredients(request, pk, run=None):
    from . import insights

    lang = _lang(request)
    words = INSIGHT_WORDS[lang]
    dish = get_object_or_404(_dishes(), pk=pk)
    from django.db.models import Q

    kept = dish.dish_ingredients.values_list("ingredient_id", flat=True)  # one made inactive since still shows, so the page saves
    ingredients = Item.objects.filter(Q(active=True) | Q(pk__in=kept), is_stock_tracked=True).order_by("item_name")
    from . import kitchen

    kw = KITCHEN_WORDS[lang]
    error = ""
    if request.method == "POST" and request.POST.get("action") == "cancel_preparation":  # HG-107
        return _cancel_preparation(request, dish, lang)
    if request.method == "POST" and request.POST.get("action") == "groups":  # R1a: its own form; never reaches the ingredients' save below
        if not user_has_permission(request.user, TABLES):
            raise PermissionDenied("Changing a dish's add-ons and choices needs master_data.manage_items.")
        from .views_modifiers import save_dish_groups

        return save_dish_groups(request, dish, lang)
    if request.method == "POST" and request.POST.get("action") in ("kitchen", "prepare"):
        try:
            if request.POST["action"] == "kitchen":  # RESTO-004
                dish = kitchen.make_in_kitchen(dish, request.user, lang)
                messages.success(request, kw["switched"])
            else:
                run = kitchen.prepare(dish, request.POST.get("quantity", ""), request.user, lang)
                from settings_core.templatetags.hesba_format import qty

                messages.success(request, kw["prepared"].format(quantity=qty(run.output_quantity)))
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
        return redirect(f"{reverse('restaurant:dish', args=[dish.pk])}?lang={lang}")
    if request.method == "POST":
        if not user_has_permission(request.user, TABLES):
            raise PermissionDenied("Changing a dish's ingredients needs master_data.manage_items.")
        chosen = ingredients.in_bulk([int(raw) for raw in request.POST.getlist("ingredient") if str(raw).isdigit()])
        lines = [(chosen.get(int(raw)) if str(raw).isdigit() else None, qty)
                 for raw, qty in zip(request.POST.getlist("ingredient"), request.POST.getlist("quantity"))]
        try:
            insights.save_ingredients(dish, lines, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('restaurant:costing')}?lang={lang}")
    from .views_modifiers import dish_groups_context

    current = list(dish.dish_ingredients.select_related("ingredient"))
    rows = [{"ingredient": row.ingredient_id, "quantity": row.quantity} for row in current] + [{"ingredient": None, "quantity": ""}] * 3
    if request.method == "POST":
        rows = [{"ingredient": int(raw) if str(raw).isdigit() else None, "quantity": qty}
                for raw, qty in zip(request.POST.getlist("ingredient"), request.POST.getlist("quantity"))]
    return render(request, "restaurant/dish_ingredients.html", _base(request, page_title=dish.item_name, iw=words, dish=dish, rows=rows,
                                                                   ingredients=ingredients, cost=insights.plate_cost(dish, current), error=error,
                                                                   can_edit=user_has_permission(request.user, TABLES), kw=kw,
                                                                   kitchen_made=kitchen.is_kitchen_made(dish), in_stock=_in_stock(dish),
                                                                   preparations=_preparations(dish, lang, run), can_cancel=kitchen.may_cancel(request.user),
                                                                   **dish_groups_context(dish, lang)))


def _preparations(dish, lang, asked=None):
    """HG-107: the dish's recent preparations, each with why it cannot be cancelled now and whether a request waits.

    ``asked``: a preparation the person was sent here for (from the stock screen or the approvals inbox). It is listed
    first even when it is older than the recent ones, so a preparation that can still be cancelled is never out of reach;
    one that is not this dish's here is a 404."""

    from approvals.models import ApprovalRequest, ApprovalStatus
    from django.http import Http404

    from . import kitchen

    runs = kitchen.preparations(dish)
    if asked is not None:
        found = kitchen.preparation_of(dish, asked)
        if found is None:
            raise Http404("Not one of this dish's preparations here.")
        runs = [found] + [run for run in runs if run.pk != found.pk]
    waiting = set(ApprovalRequest.objects.filter(action=kitchen.CANCEL_RUN, status=ApprovalStatus.PENDING, object_id__in=[run.pk for run in runs])
                  .values_list("object_id", flat=True))
    return [{"run": run, "posted": run.status == "posted", "why_not": kitchen.why_not(run, lang), "waiting": run.pk in waiting} for run in runs]


def _cancel_preparation(request, dish, lang):
    """HG-107: «إلغاء التحضير». The order is the run page's (``manufacturing.views.run_detail``): who may, the run in
    this entity and of this dish, what ``cancel_run`` would refuse said now, then the approval hold, then the cancel."""

    from approvals.gate import held
    from django.http import Http404
    from manufacturing.services import check_cancellable
    from settings_core.ui_messages import translate

    from . import kitchen

    back = redirect(f"{reverse('restaurant:dish', args=[dish.pk])}?lang={lang}")
    words = kitchen._words(lang)
    if not kitchen.may_cancel(request.user):
        messages.error(request, words["cancel_manage"])
        return back
    run = kitchen.preparation_of(dish, request.POST.get("run")) if kitchen.on_menu(dish) else None
    if run is None:
        raise Http404("Not one of this dish's preparations here.")
    try:
        reason = check_cancellable(run, request.POST.get("reason", ""), lang)
        if held(request, kitchen.CANCEL_RUN, run.pk, {"reason": reason, "lang": lang}, run.number):
            return back
        kitchen.cancel_preparation(run, request.user, reason, lang, dish=dish)
    except ValidationError as exc:
        messages.error(request, translate(" ".join(exc.messages), lang))
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, KITCHEN_WORDS[lang]["cancelled"])
    return back


def _in_stock(dish):
    """Plates of a kitchen dish ready in the selling location, else None."""

    from inventory.services import get_item_location_stock_quantity

    from . import kitchen

    if not dish.is_stock_tracked:
        return None
    location = kitchen._location()
    return get_item_location_stock_quantity(dish, location) if location is not None else None


def _period(request, words):
    from datetime import date

    today = timezone.localdate()
    first, last, note = today.replace(day=1), today, ""
    if request.GET.get("from") or request.GET.get("to"):
        try:
            last = min(date.fromisoformat(request.GET.get("to")), today) if request.GET.get("to") else last  # days still to come never dilute it
            first = date.fromisoformat(request.GET.get("from")) if request.GET.get("from") else last.replace(day=1)
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
    # TRACK-ACTS W14: the report counts restaurant orders only; name the rest so it agrees with the dashboard.
    from reports.selectors import profit_totals
    from settings_core.templatetags.hesba_format import money

    all_sales = profit_totals(date_from=first, date_to=last)["sales"]
    others = words["others"].format(other=money(all_sales - data["totals"]["sales"]), all=money(all_sales)) if all_sales > data["totals"]["sales"] else ""
    return render(request, "restaurant/report.html", _base(request, page_title=words["report"], iw=words, first=first, last=last, note=note, others=others,
                                                         deliveries=insights.deliveries(first, last), dw=DELIVERY_WORDS[lang],
                                                         today=timezone.localdate(), waste=waste_rows, waste_total=waste_total,
                                                         can_cost=user_has_permission(request.user, COST_VIEW), **data))
