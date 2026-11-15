"""Restaurants R7 (HG-116): the new-delivery screen (the caller by number) and the delivery zones' set-up.

The call screen is for whoever takes orders (``sales.create_sales_invoice`` or ``restaurant.take_orders``), for reading as much
as for writing: a number looked up shows a customer's addresses and orders, so a user who may not take orders gets a 403.
Adding a new customer also needs ``master_data.manage_parties``, as the till requires; the customer's balance shows only with
``reports.view_customer_report``. The zones' set-up follows the kitchen stations': seen with the invoices' view right, changed
with ``master_data.manage_items``. Nothing here posts anything: the delivery fee is the order's own and is billed at payment.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from master_data.models import Customer
from permissions.decorators import require_any_permission, require_permission
from permissions.services import user_has_permission

from . import branches, brands, callcentre, services
from .models import DeliveryZone
from .views import TABLES, TAKE, TAKE_ORDERS, VIEW, _base, _lang, _order_url

CALL_WORDS = {
    "ar": {
        "title": "دليفري جديد", "intro": "اكتب رقم العميل اللي بيتصل، وهتلاقي عناوينه وآخر طلباته.", "back": "الطاولات", "phone": "رقم الموبايل", "find": "دوّر",
        "branch": "الفرع", "choose_branch": "اختار الفرع", "pick_branch": "اختار الفرع الأول عشان تفتح الطلب.",
        "found": "العميل", "balance": "الرصيد", "owes": "عليه", "credit": "له", "settled": "مسدّد", "no_phone": "من غير رقم",
        "new_title": "عميل جديد", "new_hint": "مفيش عميل بالرقم ده. سجّله بالاسم والرقم.", "name": "الاسم", "add_customer": "ضيف العميل",
        "no_card": "من غير كارت عميل", "no_card_hint": "اكتب العنوان وافتح الدليفري برقم العميل بس؛ صاحب صلاحية العملاء يقدر يسجّله بعدين.",
        "addresses": "العناوين", "no_addresses": "لسه مفيش عناوين محفوظة. ضيف أول عنوان.", "default": "الافتراضي", "zone": "المنطقة", "no_zone": "من غير منطقة",
        "fee": "رسوم التوصيل", "usual": "التوصيل في العادة", "min": "دقيقة", "start": "افتح الطلب", "make_default": "خليه الافتراضي", "remove": "شيل العنوان",
        "remove_confirm": "تشيل العنوان ده؟", "add_address": "ضيف عنوان", "label": "الاسم (البيت، الشغل…)", "address": "العنوان", "area": "المنطقة أو الحي",
        "landmark": "علامة مميزة", "save": "حفظ", "make_default_new": "خليه العنوان الافتراضي", "card_address": "العنوان المكتوب على كارت العميل",
        "last_orders": "آخر الطلبات", "no_orders": "مفيش طلبات مدفوعة قبل كده.", "repeat": "كرر", "repeat_to": "على عنوان", "dishes": "أصناف",
        "repeat_hint": "الأصناف بتتحط بأسعار النهارده ولسه ما بتروحش المطبخ.", "need_address": "ضيف عنوان الأول عشان تكرر الطلب.",
        "typed_title": "دليفري برقم وعنوان", "open_delivery": "افتح الدليفري", "not_found": "مفيش عميل بالرقم ده.",
        "zones_link": "مناطق التوصيل", "call_link": "دليفري جديد", "brand": "البراند",
    },
    "en": {
        "title": "New delivery", "intro": "Type the caller's number to see their addresses and last orders.", "back": "Tables", "phone": "Mobile number", "find": "Look up",
        "branch": "Branch", "choose_branch": "Choose the branch", "pick_branch": "Choose the branch first to open the order.",
        "found": "Customer", "balance": "Balance", "owes": "Owes", "credit": "Credit", "settled": "Settled", "no_phone": "No number",
        "new_title": "New customer", "new_hint": "No customer has this number. Add them with a name and the number.", "name": "Name", "add_customer": "Add the customer",
        "no_card": "Without a customer card", "no_card_hint": "Type the address and open the delivery with the caller's number only; someone with the customers right can add them later.",
        "addresses": "Addresses", "no_addresses": "No saved addresses yet. Add the first one.", "default": "Default", "zone": "Zone", "no_zone": "No zone",
        "fee": "Delivery fee", "usual": "Usually delivered in", "min": "min", "start": "Open the order", "make_default": "Make default", "remove": "Remove address",
        "remove_confirm": "Remove this address?", "add_address": "Add an address", "label": "Name (Home, Work…)", "address": "Address", "area": "Area or district",
        "landmark": "Landmark", "save": "Save", "make_default_new": "Make it the default address", "card_address": "The address written on the customer's card",
        "last_orders": "Last orders", "no_orders": "No paid orders before.", "repeat": "Repeat", "repeat_to": "To address", "dishes": "items",
        "repeat_hint": "Items go in at today's prices and are not sent to the kitchen yet.", "need_address": "Add an address first to repeat an order.",
        "typed_title": "Delivery by number and address", "open_delivery": "Open the delivery", "not_found": "No customer has this number.",
        "zones_link": "Delivery zones", "call_link": "New delivery", "brand": "Brand",
    },
}
ZONE_WORDS = {
    "ar": {
        "title": "مناطق التوصيل", "intro": "كل منطقة ليها رسوم توصيل ووقت معتاد. العنوان المربوط بمنطقة بيفتح الدليفري برسومها، وتقدر تعدّلها على الطلب.",
        "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي", "fee": "رسوم التوصيل", "minutes": "الوقت المعتاد (دقيقة)", "active": "شغالة", "branch": "الفرع",
        "edit": "تعديل", "delete": "امسح", "delete_confirm": "تمسح المنطقة دي؟", "save": "حفظ", "new": "منطقة جديدة", "none": "لسه مفيش مناطق. ضيف أول منطقة.",
        "back": "الطاولات", "used": "عناوين", "choose_branch": "اختار الفرع", "call": "دليفري جديد",
    },
    "en": {
        "title": "Delivery zones", "intro": "Each zone has a delivery fee and a usual time. An address tied to a zone opens the delivery with its fee, which can still be changed on the order.",
        "name_ar": "Name in Arabic", "name_en": "Name in English", "fee": "Delivery fee", "minutes": "Usual time (min)", "active": "Active", "branch": "Branch",
        "edit": "Edit", "delete": "Delete", "delete_confirm": "Delete this zone?", "save": "Save", "new": "New zone", "none": "No zones yet. Add the first one.",
        "back": "Tables", "used": "addresses", "choose_branch": "Choose the branch", "call": "New delivery",
    },
}


def _call_url(lang, phone="", entity=None):
    query = {"lang": lang}
    if phone:
        query["phone"] = phone
    if entity is not None:
        query["entity"] = entity.pk
    return f"{reverse('restaurant:call')}?{urlencode(query)}"


def _customer(raw):
    raw = str(raw or "")
    return Customer.objects.filter(pk=raw, active=True).first() if raw.isdigit() else None


def _target(entity):
    return entity or branches.working_entity()


def _act(request, lang, entity, phone):
    """One POST on the call screen. Returns the redirect, or None to show the page again."""

    action = request.POST.get("action", "")
    words = callcentre.MESSAGES[lang]
    customer = _customer(request.POST.get("customer"))
    back = _call_url(lang, phone, entity)
    grouped = branches.group_mode()
    if grouped and entity is None and action in ("open", "repeat", "open_typed"):
        messages.error(request, CALL_WORDS[lang]["pick_branch"])
        return redirect(back)
    try:
        if action == "new_customer":
            customer, created = callcentre.new_caller(request.POST.get("name"), request.POST.get("phone"), request.user, lang)
            if created:
                messages.success(request, words["customer_added"].format(name=customer.name))
            return redirect(_call_url(lang, callcentre.clean_phone(request.POST.get("phone"), lang), entity))
        if action == "open_typed":  # no card: the number and the address as typed
            order = callcentre.open_delivery(request.user, text=request.POST.get("address", ""), phone=callcentre.clean_phone(request.POST.get("phone"), lang),
                                             fee=request.POST.get("delivery_fee"), entity=entity if grouped else None,
                                             brand=request.POST.get("brand"), lang=lang)
            messages.success(request, words["opened"].format(number=order.number))
            return redirect(_order_url(order, lang))
        if customer is None:
            raise ValidationError(words["customer_gone"])
        if action == "add_address":
            callcentre.save_address(customer, {key: request.POST.get(key, "") for key in ("label", "address", "area", "landmark", "zone")}
                                    | {"is_default": request.POST.get("is_default") == "on"}, request.user, lang)
            messages.success(request, words["address_saved"])
        elif action == "default":
            callcentre.make_default(customer, request.POST.get("address"), request.user, lang)
            messages.success(request, words["address_default"])
        elif action == "remove":
            callcentre.remove_address(customer, request.POST.get("address"), request.user, lang)
            messages.success(request, words["address_off"])
        elif action == "open":
            order = callcentre.open_delivery(request.user, customer=customer, address=request.POST.get("address"), fee=request.POST.get("delivery_fee"),
                                             entity=entity if grouped else None, brand=request.POST.get("brand"), lang=lang)
            messages.success(request, words["opened_fee"].format(number=order.number, fee=f"{order.delivery_fee:,.2f}") if order.delivery_fee else words["opened"].format(number=order.number))
            return redirect(_order_url(order, lang))
        elif action == "repeat":
            source = callcentre.own_past_order(customer, request.POST.get("order"))
            if source is None:
                raise ValidationError(words["repeat_gone"])
            order, count, skipped = callcentre.repeat_as_delivery(request.user, source, customer=customer, address=request.POST.get("address"),
                                                                  fee=request.POST.get("delivery_fee"), entity=entity if grouped else None,
                                                                  brand=request.POST.get("brand"), lang=lang)
            if count:
                messages.success(request, words["repeat_done"].format(count=count, number=source.number))
            else:
                messages.warning(request, words["repeat_none"].format(number=source.number))
            if skipped:
                messages.warning(request, f"{words['skipped']} " + " ".join(skipped))
            return redirect(_order_url(order, lang))
        else:
            raise ValidationError(words["customer_gone"])
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect(back)


def _with_brand(rows, options):
    """R11: «كرر» offers the brand the past order was sold under when it is still one of the branch's active brands, else the branch's default."""

    pks = {one["pk"] for one in options}
    default = next((one["pk"] for one in options if one["default"]), None)
    for row in rows:
        row["brand"] = row["order"].brand_id if row["order"].brand_id in pks else default
    return rows


@require_any_permission(TAKE, TAKE_ORDERS)
def call(request):
    lang = _lang(request)
    cw = CALL_WORDS[lang]
    grouped = branches.group_mode()
    source = request.POST if request.method == "POST" else request.GET
    entity = branches.pick(request.user, source.get("entity")) if grouped else None
    phone = (source.get("phone") or "").strip()[:50]
    if request.method == "POST":
        return _act(request, lang, entity, phone)
    can_open = not grouped or entity is not None
    options = [{"pk": one.pk, "label": one.label(lang), "default": one.is_default} for one in brands.offered(_target(entity))] if can_open else []  # R11 (HG-117)
    context = {"cw": cw, "phone": phone, "brand_options": options, "grouped": grouped, "entity": entity, "branch_choices": branches.choices(request.user, lang) if grouped else [],
               "can_add_customer": user_has_permission(request.user, callcentre.MANAGE_PARTIES), "searched": bool(phone), "error": "",
               "can_open": can_open, "page_title": cw["title"]}
    customer = None
    if phone:
        try:
            typed = callcentre.clean_phone(phone, lang)
            customer = callcentre.find_caller(typed)
            context["typed"] = typed
        except ValidationError as exc:
            context["error"] = " ".join(exc.messages)
            context["searched"] = False
    if customer is not None:
        target = _target(entity) if (not grouped or entity is not None) else None
        saved = callcentre.addresses_of(customer)
        rows = []
        for row in saved:
            zone = callcentre.zone_for(row, target) if target is not None else None
            rows.append({"address": row, "text": callcentre.address_text(row), "zone": row.zone, "zone_label": row.zone.label(lang) if row.zone else "",
                         "zone_ok": zone is not None, "fee": f"{zone.fee:.2f}" if zone is not None else "0"})
        balance = callcentre.customer_balance(customer, request.user)
        card = (customer.address or "").replace("\r", " ").replace("\n", ", ").strip()[:255]
        context.update(customer=customer, rows=rows, balance=balance, balance_abs=abs(balance) if balance is not None else None,
                       orders=_with_brand(callcentre.last_orders(customer), options), zone_choices=[{"pk": zone.pk, "label": f"{zone.label(lang)} · {zone.fee:.2f}" + (f" · {zone.entity.display(lang)}" if grouped and entity is None else "")}
                                                                             for zone in callcentre.offered_zones(entity if grouped else None)],
                       card_address=card if not saved else "")
    return render(request, "restaurant/call.html", _base(request, **context))


@require_permission(VIEW)
def zone_setup(request):
    lang = _lang(request)
    zw = ZONE_WORDS[lang]
    words = callcentre.MESSAGES[lang]
    error = ""
    grouped = branches.group_mode()
    editing = None
    # a POST acts on the row it names (a row's delete form keeps the page's ``?edit=``); ``edit`` only opens the form on a GET
    raw = str((request.POST.get("pk") if request.method == "POST" else request.GET.get("edit")) or "")
    if raw.isdigit():
        editing = get_object_or_404(callcentre.zones(), pk=int(raw))  # R12: another branch's zone is a 404
    form = {"fee": "0", "active": True}
    if request.method == "POST":
        if not user_has_permission(request.user, TABLES):
            raise PermissionDenied("Changing the delivery zones needs master_data.manage_items.")
        form = {"name_ar": request.POST.get("name_ar", ""), "name_en": request.POST.get("name_en", ""), "fee": request.POST.get("fee", ""),
                "minutes": request.POST.get("minutes", ""), "active": request.POST.get("active") == "on", "entity": request.POST.get("entity", "")}
        try:
            if request.POST.get("action") == "delete":
                if editing is None:
                    raise ValidationError(words["zone_foreign"])
                callcentre.delete_zone(editing, request.user, lang)
                messages.success(request, words["zone_deleted"])
            else:
                entity = branches.pick(request.user, request.POST.get("entity")) if grouped else None
                if grouped and entity is None and editing is None:
                    raise ValidationError(services.MESSAGES[lang]["branch"])
                callcentre.save_zone(form, request.user, editing, lang, entity=entity)
                messages.success(request, words["zone_saved"])
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            return redirect(f"{reverse('restaurant:zones')}?lang={lang}")
    elif editing:
        form = {"name_ar": editing.name_ar, "name_en": editing.name_en, "fee": f"{editing.fee:.2f}", "minutes": editing.minutes or "", "active": editing.active}
    rows = callcentre.zones(DeliveryZone.objects.select_related("entity").annotate(used=Count("addresses")))
    return render(request, "restaurant/zones.html", _base(request, page_title=zw["title"], zw=zw, rows=rows, form=form, editing=editing, error=error,
                                                         can_edit=user_has_permission(request.user, TABLES), grouped=grouped,
                                                         branch_choices=branches.choices(request.user, lang) if grouped else [], chosen_branch=str(form.get("entity") or "")))

